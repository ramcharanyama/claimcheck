"""Paperwork checks for ClaimCheck v3: catch mistakes in the claim documents themselves.

These are not policy rules, so they never deduct money (estimated_impact is always None).
They read the `document_details` fact (one entry per uploaded document) and, where
relevant, `claim_amount_summary`, and return Findings whose rule_id starts with CHECK_.

Each entry of `document_details` looks like:
    {"document": "hospital_bill.pdf", "document_type": "hospital_bill", "page": 1,
     "quote": "Patient: Priya Patel  Policy No: POL-AURA-2026-001",
     "patient_name": "Priya Patel", "policy_number": "POL-AURA-2026-001",
     "admission_date": "2026-03-10", "discharge_date": "2026-03-14",
     "stated_total": 47400, "line_items": [{"item": "Room charges", "amount": 30000}, ...]}
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.engine.coverage_rules import _date, _dec, _money
from src.schemas import ClaimFact, Finding, FindingStatus, SourceRef

CHECK_LABELS = {
    "CHECK_PATIENT_NAME": "Patient name matches on every document",
    "CHECK_POLICY_NUMBER": "Policy number matches the policy",
    "CHECK_DATES": "Admission and discharge dates are consistent",
    "CHECK_BILL_ARITHMETIC": "Bill line items add up to the bill total",
    "CHECK_CLAIMED_VS_BILLED": "Amount claimed matches the bills",
    "CHECK_DUPLICATE_CHARGES": "No charge is billed twice",
}
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "kumari", "master", "baby"}
_BILL_TYPES = {"hospital_bill", "final_bill", "pharmacy_bill", "pre_hospitalization_bill",
               "post_hospitalization_bill", "ambulance_bill", "bill", "invoice", "cost_estimate"}


def _entries(facts: Dict[str, ClaimFact]) -> List[dict]:
    fact = facts.get("document_details")
    value = fact.value if fact else None
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def _source(entry: Optional[dict], fallback: Optional[SourceRef]) -> SourceRef:
    if entry and entry.get("document") and entry.get("quote"):
        try:
            return SourceRef(document=str(entry["document"]), page=int(entry.get("page") or 1),
                             quote=str(entry["quote"]))
        except Exception:
            pass
    return fallback or SourceRef(document="claim documents", page=1, quote="(no quote available)")


def _f(check_id: str, status: FindingStatus, inputs: Any, steps: List[str], explanation: str,
       source: SourceRef) -> Finding:
    return Finding(rule_id=check_id, status=status, inputs_used=inputs or {}, calculation_steps=steps,
                   explanation=explanation, source=source, estimated_impact=None)


def _name_tokens(name: Any) -> List[str]:
    return [t for t in re.split(r"[^a-z]+", str(name or "").lower()) if t and t not in _TITLES]


_OCR_CONFUSIONS = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o", "5": "s", "8": "b"})


def _ocr_fold(token: str) -> str:
    """Fold characters that scanners commonly confuse (Iyer / lyer, O / 0)."""
    return token.translate(_OCR_CONFUSIONS)


def _one_edit_apart(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _names_relation(ref: List[str], toks: List[str]) -> str:
    """'same', 'similar' (likely the same person, please confirm) or 'different'."""
    fr, ft = [_ocr_fold(t) for t in ref], [_ocr_fold(t) for t in toks]
    if fr == ft:
        return "same"
    if set(ft) <= set(fr) or set(fr) <= set(ft):
        return "similar"            # e.g. "Priya Patel" vs "Priya S Patel"
    if len(fr) == len(ft) and all(a == b or _one_edit_apart(a, b) for a, b in zip(fr, ft)):
        return "similar"            # one-letter slip, e.g. "Iyer" vs "Iyar"
    return "different"


def _norm_policy(num: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(num or "").upper())


# --------------------------------------------------------------------------- individual checks
def check_patient_name(entries: List[dict], fallback: Optional[SourceRef]) -> Optional[Finding]:
    named = [(e, _name_tokens(e.get("patient_name"))) for e in entries if _name_tokens(e.get("patient_name"))]
    if len(named) < 2:
        return None
    steps = [f"{e.get('document')}: '{e.get('patient_name')}'" for e, _ in named]
    ref_entry, ref = named[0]
    hard, soft = [], []
    for e, toks in named[1:]:
        relation = _names_relation(ref, toks)
        if relation == "similar":
            soft.append(e)
        elif relation == "different":
            hard.append(e)
    inputs = {e.get("document"): e.get("patient_name") for e, _ in named}
    if hard:
        e = hard[0]
        return _f("CHECK_PATIENT_NAME", FindingStatus.VIOLATED, inputs, steps,
                  f"The patient name is '{ref_entry.get('patient_name')}' on {ref_entry.get('document')} but "
                  f"'{e.get('patient_name')}' on {e.get('document')}. Correct the documents before submitting, "
                  "or the insurer may reject the claim.", _source(e, fallback))
    if soft:
        e = soft[0]
        return _f("CHECK_PATIENT_NAME", FindingStatus.NEEDS_REVIEW, inputs, steps,
                  f"The patient name is written slightly differently ('{ref_entry.get('patient_name')}' vs "
                  f"'{e.get('patient_name')}'). Please confirm it is the same person.", _source(e, fallback))
    return _f("CHECK_PATIENT_NAME", FindingStatus.SATISFIED, inputs, steps,
              f"The patient name '{ref_entry.get('patient_name')}' is the same on every document.",
              _source(ref_entry, fallback))


def check_policy_number(entries: List[dict], policy_number: Optional[str],
                        fallback: Optional[SourceRef]) -> Optional[Finding]:
    with_num = [e for e in entries if _norm_policy(e.get("policy_number"))]
    if not with_num:
        return None
    expected = _norm_policy(policy_number) if policy_number else _norm_policy(with_num[0].get("policy_number"))
    steps = [f"Policy number on the policy: {policy_number or 'not available'}."] + \
            [f"{e.get('document')}: {e.get('policy_number')}" for e in with_num]
    wrong = [e for e in with_num if _norm_policy(e.get("policy_number")) != expected]
    inputs = {e.get("document"): e.get("policy_number") for e in with_num}
    if wrong:
        e = wrong[0]
        return _f("CHECK_POLICY_NUMBER", FindingStatus.VIOLATED, inputs, steps,
                  f"{e.get('document')} shows policy number {e.get('policy_number')}, but the policy is "
                  f"{policy_number or with_num[0].get('policy_number')}. Fix this before submitting.",
                  _source(e, fallback))
    return _f("CHECK_POLICY_NUMBER", FindingStatus.SATISFIED, inputs, steps,
              "The policy number matches on every document.", _source(with_num[0], fallback))


def check_dates(entries: List[dict], fallback: Optional[SourceRef]) -> Optional[Finding]:
    adm = [(e, _date(e.get("admission_date"))) for e in entries if _date(e.get("admission_date"))]
    dis = [(e, _date(e.get("discharge_date"))) for e in entries if _date(e.get("discharge_date"))]
    if not adm and not dis:
        return None
    steps = [f"{e.get('document')}: admitted {d.isoformat()}" for e, d in adm] + \
            [f"{e.get('document')}: discharged {d.isoformat()}" for e, d in dis]
    inputs = {"admission_dates": {e.get("document"): d.isoformat() for e, d in adm},
              "discharge_dates": {e.get("document"): d.isoformat() for e, d in dis}}
    problems, where = [], None
    if len({d for _, d in adm}) > 1:
        problems.append("the documents show different admission dates")
        where = adm[1][0]
    if len({d for _, d in dis}) > 1:
        problems.append("the documents show different discharge dates")
        where = where or dis[1][0]
    if adm and dis and min(d for _, d in dis) < max(d for _, d in adm):
        problems.append("the discharge date is before the admission date")
        where = where or dis[0][0]
    if problems:
        return _f("CHECK_DATES", FindingStatus.VIOLATED, inputs, steps,
                  "Date mistake: " + "; ".join(problems) + ". Correct the dates before submitting.",
                  _source(where, fallback))
    return _f("CHECK_DATES", FindingStatus.SATISFIED, inputs, steps,
              "Admission and discharge dates agree across the documents.", _source((adm or dis)[0][0], fallback))


def check_bill_arithmetic(entries: List[dict], fallback: Optional[SourceRef]) -> Optional[Finding]:
    bills = [e for e in entries if e.get("line_items") and _dec(e.get("stated_total")) is not None]
    if not bills:
        return None
    steps, wrong = [], []
    for e in bills:
        amounts = [_dec(i.get("amount")) for i in e["line_items"] if isinstance(i, dict)]
        if any(a is None for a in amounts):
            steps.append(f"{e.get('document')}: some line amounts are unreadable; skipped.")
            continue
        total = sum(amounts, Decimal("0"))
        stated = _dec(e.get("stated_total"))
        steps.append(f"{e.get('document')}: line items add up to {_money(total)}; bill says {_money(stated)}.")
        if abs(total - stated) > Decimal("1"):
            wrong.append((e, total, stated))
    if wrong:
        e, total, stated = wrong[0]
        gap = stated - total
        return _f("CHECK_BILL_ARITHMETIC", FindingStatus.VIOLATED,
                  {"document": e.get("document"), "sum_of_items": float(total), "stated_total": float(stated)}, steps,
                  f"On {e.get('document')} the items add up to {_money(total)} but the total says {_money(stated)} "
                  f"({'over' if gap > 0 else 'under'} by {_money(abs(gap))}). Ask the hospital for a corrected bill.",
                  _source(e, fallback))
    return _f("CHECK_BILL_ARITHMETIC", FindingStatus.SATISFIED, {}, steps,
              "Every bill adds up correctly.", _source(bills[0], fallback))


def check_claimed_vs_billed(entries: List[dict], facts: Dict[str, ClaimFact],
                            fallback: Optional[SourceRef]) -> Optional[Finding]:
    summary = facts.get("claim_amount_summary")
    claimed = _dec(summary.value.get("total_claimed")) if summary and isinstance(summary.value, dict) else None
    bills = [e for e in entries if str(e.get("document_type", "")).lower() in _BILL_TYPES
             and _dec(e.get("stated_total")) is not None]
    if claimed is None or not bills:
        return None
    billed = sum((_dec(e.get("stated_total")) for e in bills), Decimal("0"))
    steps = [f"Amount claimed on the form: {_money(claimed)}."] + \
            [f"{e.get('document')}: {_money(_dec(e.get('stated_total')))}" for e in bills] + \
            [f"Total of the bills: {_money(billed)}."]
    inputs = {"claimed": float(claimed), "billed": float(billed)}
    src = summary.source if summary else fallback
    if claimed > billed + Decimal("1"):
        return _f("CHECK_CLAIMED_VS_BILLED", FindingStatus.VIOLATED, inputs, steps,
                  f"The claim form asks for {_money(claimed)}, but the bills only add up to {_money(billed)}. "
                  f"You cannot claim more than you were billed; the extra {_money(claimed - billed)} will be refused.",
                  src)
    if claimed < billed - Decimal("1"):
        return _f("CHECK_CLAIMED_VS_BILLED", FindingStatus.NEEDS_REVIEW, inputs, steps,
                  f"The claim form asks for {_money(claimed)}, less than the bills total of {_money(billed)}. "
                  f"Check whether {_money(billed - claimed)} was left out by mistake.", src)
    return _f("CHECK_CLAIMED_VS_BILLED", FindingStatus.SATISFIED, inputs, steps,
              f"The amount claimed ({_money(claimed)}) matches the bills.", src)


def check_duplicate_charges(entries: List[dict], fallback: Optional[SourceRef]) -> Optional[Finding]:
    bills = [e for e in entries if e.get("line_items")]
    if not bills:
        return None
    steps, dups = [], []
    for e in bills:
        keys = Counter((re.sub(r"[^a-z0-9]+", " ", str(i.get("item", "")).lower()).strip(), str(_dec(i.get("amount"))))
                       for i in e["line_items"] if isinstance(i, dict))
        for (item, amount), n in keys.items():
            if n > 1 and item:
                dups.append((e, item, amount, n))
                steps.append(f"{e.get('document')}: '{item}' for INR {amount} appears {n} times.")
    if dups:
        e, item, amount, n = dups[0]
        return _f("CHECK_DUPLICATE_CHARGES", FindingStatus.NEEDS_REVIEW, {"duplicates": [d[1] for d in dups]}, steps,
                  f"'{item}' for INR {amount} appears {n} times on {e.get('document')}. "
                  "Check whether it was charged twice by mistake.", _source(e, fallback))
    return _f("CHECK_DUPLICATE_CHARGES", FindingStatus.SATISFIED, {}, steps or ["No repeated line items."],
              "No charge appears twice.", _source(bills[0], fallback))


def run_document_checks(facts: List[ClaimFact], policy_number: Optional[str] = None) -> List[Finding]:
    """Run every paperwork check that has enough information. Checks without data are skipped."""
    by_name = {f.name: f for f in facts}
    entries = _entries(by_name)
    fallback = by_name["document_details"].source if "document_details" in by_name else None
    results = [
        check_patient_name(entries, fallback),
        check_policy_number(entries, policy_number, fallback),
        check_dates(entries, fallback),
        check_bill_arithmetic(entries, fallback),
        check_claimed_vs_billed(entries, by_name, fallback),
        check_duplicate_charges(entries, fallback),
    ]
    return [r for r in results if r is not None]
