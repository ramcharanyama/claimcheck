"""Deterministic evaluators for the ClaimCheck v3 coverage rules (policy page 5).

Rule types: pre_hospitalization, post_hospitalization, ambulance_limit, sum_insured,
cashless_preauthorization and pre_existing_disclosure.

Same principles as the core engine: exact Decimal arithmetic, no guessing, and an
explicit unable_to_verify / needs_review result whenever the facts are missing or
too imprecise to decide. Explanations are written in plain language.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from src.schemas import ClaimFact, Finding, FindingStatus, Rule

TWO_PLACES = Decimal("0.01")


# --------------------------------------------------------------------------- helpers
def _dec(val: Any) -> Optional[Decimal]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return Decimal(str(val).replace(",", "").strip())
    except Exception:
        return None


def _money(d: Decimal) -> str:
    return f"INR {d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP):,.2f}"


def _date(val: Any) -> Optional[date]:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if not isinstance(val, str) or not val.strip():
        return None
    text = val.strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _datetime(val: Any) -> Optional[datetime]:
    """Parse a value that includes a time of day; plain dates return None."""
    if isinstance(val, datetime):
        return val
    if not isinstance(val, str) or not re.search(r"\d{1,2}:\d{2}", val):
        return None
    try:
        return datetime.fromisoformat(val.strip().replace(" ", "T", 1))
    except ValueError:
        return None


def _finding(rule: Rule, status: FindingStatus, inputs: Any, steps: List[str], explanation: str,
             impact: Optional[Decimal] = None) -> Finding:
    return Finding(
        rule_id=rule.rule_id,
        status=status,
        inputs_used=inputs if inputs is not None else {},
        calculation_steps=steps,
        explanation=explanation,
        source=rule.source,
        estimated_impact=float(impact.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)) if impact is not None else None,
    )


def _missing(rule: Rule, what: str, steps: Optional[List[str]] = None, inputs: Any = None) -> Finding:
    return _finding(rule, FindingStatus.UNABLE_TO_VERIFY, inputs,
                    (steps or []) + [f"Missing or unparseable input: {what}."],
                    f"Cannot check this rule because {what} is not available in the documents.")


def _items(value: Any) -> List[dict]:
    if isinstance(value, dict):
        value = value.get("items", [value])
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


# --------------------------------------------------------------------------- pre / post hospitalisation
def _evaluate_window(rule: Rule, facts: Dict[str, ClaimFact], *, before: bool) -> Finding:
    label = "pre-hospitalisation" if before else "post-hospitalisation"
    fact_name = "pre_hospitalization_expenses" if before else "post_hospitalization_expenses"
    anchor_key = "admission_date" if before else "discharge_date"
    window = _dec((rule.parameters or {}).get("window_days"))
    if window is None or window <= 0:
        return _missing(rule, f"a valid {label} window in the rule")

    expenses_fact = facts.get(fact_name)
    items = _items(expenses_fact.value) if expenses_fact else []
    if not items:
        return _missing(rule, f"any {label} expense in the claim")

    hosp = facts.get("hospitalization_details")
    anchor = _date(hosp.value.get(anchor_key)) if hosp and isinstance(hosp.value, dict) else None
    if anchor is None:
        return _missing(rule, f"the {anchor_key.replace('_', ' ')}",
                        [f"Found {len(items)} {label} expense(s)."], {"expenses": items})

    steps = [f"{anchor_key.replace('_', ' ').capitalize()}: {anchor.isoformat()}; allowed window: {int(window)} days "
             f"{'before' if before else 'after'} it."]
    outside, undated, inside = Decimal("0"), [], Decimal("0")
    outside_names = []
    for item in items:
        amount = _dec(item.get("amount"))
        when = _date(item.get("date"))
        name = item.get("description") or item.get("item") or "expense"
        if amount is None or when is None:
            undated.append(name)
            steps.append(f"'{name}': date or amount missing, cannot place it in the window.")
            continue
        days = (anchor - when).days if before else (when - anchor).days
        if days < 0:
            undated.append(name)
            steps.append(f"'{name}' on {when.isoformat()} is on the wrong side of the {anchor_key.replace('_', ' ')}; "
                         "needs checking.")
            continue
        if days > window:
            outside += amount
            outside_names.append(name)
            steps.append(f"'{name}' on {when.isoformat()}: {days} days {'before' if before else 'after'} "
                         f"- outside the {int(window)}-day window, {_money(amount)} not payable.")
        else:
            inside += amount
            steps.append(f"'{name}' on {when.isoformat()}: {days} days - inside the window, {_money(amount)} payable.")

    inputs = {"expenses": items, anchor_key: anchor.isoformat(), "window_days": int(window)}
    if outside > 0:
        steps.append(f"Total outside the window: {_money(outside)}.")
        return _finding(rule, FindingStatus.VIOLATED, inputs, steps,
                        f"Billed more than {int(window)} days {'before admission' if before else 'after discharge'}: "
                        f"{', '.join(outside_names)}. {_money(outside)} is not covered.", outside)
    if undated:
        return _finding(rule, FindingStatus.NEEDS_REVIEW, inputs, steps,
                        f"Some {label} expenses ({', '.join(undated)}) have no usable date, so a person must check "
                        "whether they fall inside the covered window.")
    return _finding(rule, FindingStatus.SATISFIED, inputs, steps,
                    f"All {label} expenses fall inside the {int(window)}-day window and are covered.", Decimal("0"))


def evaluate_pre_hospitalization(rule: Rule, facts: Dict[str, ClaimFact]) -> Finding:
    return _evaluate_window(rule, facts, before=True)


def evaluate_post_hospitalization(rule: Rule, facts: Dict[str, ClaimFact]) -> Finding:
    return _evaluate_window(rule, facts, before=False)


# --------------------------------------------------------------------------- ambulance
def evaluate_ambulance_limit(rule: Rule, facts: Dict[str, ClaimFact]) -> Finding:
    limit = _dec((rule.parameters or {}).get("max_limit_inr"))
    if limit is None or limit <= 0:
        return _missing(rule, "a valid ambulance limit in the rule")
    fact = facts.get("ambulance_charges")
    if fact is None:
        return _missing(rule, "the ambulance charge")
    value = fact.value
    amounts = [_dec(i.get("amount")) for i in _items(value)] if isinstance(value, list) else \
        [_dec(value.get("amount") if isinstance(value, dict) else value)]
    if not amounts or any(a is None for a in amounts):
        return _missing(rule, "a readable ambulance amount", inputs={"ambulance_charges": value})
    billed = sum(amounts, Decimal("0"))
    steps = [f"Ambulance charges billed: {_money(billed)}.", f"Policy limit: {_money(limit)} per hospitalisation."]
    inputs = {"ambulance_billed": float(billed), "limit": float(limit)}
    if billed > limit:
        excess = billed - limit
        steps.append(f"Excess: {_money(billed)} - {_money(limit)} = {_money(excess)} not payable.")
        return _finding(rule, FindingStatus.VIOLATED, inputs, steps,
                        f"The ambulance cost {_money(billed)}, but the policy pays at most {_money(limit)}, "
                        f"so {_money(excess)} is not covered.", excess)
    return _finding(rule, FindingStatus.SATISFIED, inputs, steps,
                    f"The ambulance charge of {_money(billed)} is within the {_money(limit)} limit.", Decimal("0"))


# --------------------------------------------------------------------------- sum insured
def evaluate_sum_insured(rule: Rule, facts: Dict[str, ClaimFact]) -> Finding:
    si = _dec((rule.parameters or {}).get("sum_insured_inr"))
    if si is None or si <= 0:
        return _missing(rule, "a valid sum insured in the rule")
    fact = facts.get("claim_amount_summary")
    total = _dec(fact.value.get("total_claimed")) if fact and isinstance(fact.value, dict) else None
    if total is None:
        return _missing(rule, "the total amount claimed")
    previous = _dec(fact.value.get("previously_paid_this_year")) or Decimal("0")
    used = total + previous
    steps = [f"Amount claimed now: {_money(total)}.", f"Already paid this policy year: {_money(previous)}.",
             f"Total against the sum insured: {_money(used)} of {_money(si)}."]
    inputs = {"total_claimed": float(total), "previously_paid_this_year": float(previous), "sum_insured": float(si)}
    if used > si:
        excess = used - si
        steps.append(f"Excess over the sum insured: {_money(excess)}.")
        return _finding(rule, FindingStatus.VIOLATED, inputs, steps,
                        f"This claim would take total payouts this year to {_money(used)}, above the "
                        f"{_money(si)} sum insured, so {_money(excess)} cannot be paid.", excess)
    return _finding(rule, FindingStatus.SATISFIED, inputs, steps,
                    f"The claim fits within the remaining sum insured ({_money(si - used)} left after this claim).",
                    Decimal("0"))


# --------------------------------------------------------------------------- cashless pre-authorisation
_DOC_ALIASES = {
    "preauthorization_form": ("preauthorization_form", "pre_authorization_form", "preauthorisation_form",
                              "pre_authorisation_form", "preauth_form", "pre_auth_form"),
    "cost_estimate": ("cost_estimate", "doctor_estimate", "doctors_cost_estimate", "treatment_estimate",
                      "estimate", "doctor_cost_estimate"),
}


def evaluate_cashless_preauthorization(rule: Rule, facts: Dict[str, ClaimFact]) -> Finding:
    params = rule.parameters or {}
    fact = facts.get("preauthorization_request")
    if fact is None or not isinstance(fact.value, dict):
        return _missing(rule, "the pre-authorisation request details")
    req = fact.value
    present = {_norm(d) for d in (req.get("documents_present") or [])}
    steps, problems, review = [], [], []

    for doc in params.get("required_documents", []):
        aliases = _DOC_ALIASES.get(doc, (doc,))
        if any(a in present for a in aliases):
            steps.append(f"Required document present: {doc.replace('_', ' ')}.")
        else:
            steps.append(f"Required document missing: {doc.replace('_', ' ')}.")
            problems.append(f"the {doc.replace('_', ' ')} is missing")

    kind = _norm(req.get("hospitalization_type"))
    planned_h = _dec(params.get("planned_notice_hours_before"))
    emergency_h = _dec(params.get("emergency_notice_hours_after"))
    req_dt, adm_dt = _datetime(req.get("request_datetime")), _datetime(req.get("admission_datetime"))
    req_d = _date(req.get("request_datetime") or req.get("request_date"))
    adm_d = _date(req.get("admission_datetime") or req.get("admission_date"))

    if kind not in ("planned", "emergency"):
        review.append("it is not clear whether the admission is planned or an emergency")
    elif req_d is None or adm_d is None:
        review.append("the request time or admission time is missing")
    elif req_dt and adm_dt:
        hours = Decimal(str(round((adm_dt - req_dt).total_seconds() / 3600, 2)))
        if kind == "planned":
            steps.append(f"Planned admission: request sent {hours} hours before admission (needs {planned_h}).")
            if hours < planned_h:
                problems.append(f"the request was sent only {hours} hours before a planned admission "
                                f"(the policy needs {planned_h} hours)")
        else:
            after = -hours
            steps.append(f"Emergency admission: request sent {after} hours after admission (allowed {emergency_h}).")
            if after > emergency_h:
                problems.append(f"the request was sent {after} hours after an emergency admission "
                                f"(the policy allows {emergency_h} hours)")
    else:
        days = (adm_d - req_d).days
        if kind == "planned":
            steps.append(f"Planned admission: request dated {days} day(s) before admission; no times given.")
            # With dates only, N calendar days apart means anywhere from (N-1)*24 to (N+1)*24 hours.
            # Decide only when every possible time of day gives the same answer.
            if (days - 1) * 24 >= planned_h:
                pass
            elif (days + 1) * 24 < planned_h:
                problems.append(f"the request was sent only {days} day(s) before a planned admission "
                                f"(the policy needs {planned_h} hours)")
            else:
                review.append(f"the request is close to the {planned_h}-hour limit and no times are given")
        else:
            after_days = -days
            steps.append(f"Emergency admission: request dated {after_days} day(s) after admission; no times given.")
            if (after_days - 1) * 24 >= emergency_h:
                problems.append(f"the request was sent {after_days} days after an emergency admission "
                                f"(the policy allows {emergency_h} hours)")
            elif (after_days + 1) * 24 > emergency_h and after_days > 0:
                review.append("the request is dated after the admission day and no times are given")

    inputs = {"preauthorization_request": req}
    if problems:
        return _finding(rule, FindingStatus.VIOLATED, inputs, steps,
                        "Cashless approval cannot be given as requested: " + "; ".join(problems) + ".")
    if review:
        return _finding(rule, FindingStatus.NEEDS_REVIEW, inputs, steps,
                        "A person needs to check the pre-authorisation because " + "; ".join(review) + ".")
    return _finding(rule, FindingStatus.SATISFIED, inputs, steps,
                    "The pre-authorisation request has both required documents and was sent in time.")


# --------------------------------------------------------------------------- disclosure of existing conditions
_CONDITION_STOPWORDS = {"type", "chronic", "known", "case", "history", "with", "disease", "disorder", "mellitus",
                        "primary", "essential", "since", "years", "year", "of", "and", "the", "on"}


def _condition_tokens(text: Any) -> set:
    return {t for t in _norm(text).split("_") if len(t) > 2 and t not in _CONDITION_STOPWORDS}


def evaluate_pre_existing_disclosure(rule: Rule, facts: Dict[str, ClaimFact]) -> Finding:
    found_fact = facts.get("medical_conditions_found")
    found = _items(found_fact.value) if found_fact else []
    declared_fact = facts.get("declared_conditions")

    if not found and declared_fact is None:
        return _missing(rule, "information about existing medical conditions")
    if not found:
        return _finding(rule, FindingStatus.SATISFIED, {}, ["No existing medical conditions found in the records."],
                        "No existing medical conditions were found in the medical records.", Decimal("0"))
    if declared_fact is None:
        names = ", ".join(str(c.get("condition")) for c in found)
        return _missing(rule, "what the patient declared on the claim or pre-authorisation form",
                        [f"Conditions found in records: {names}."], {"conditions_found": found})

    declared_raw = declared_fact.value
    if isinstance(declared_raw, dict):
        declared_raw = declared_raw.get("conditions", [])
    declared = [d for d in (declared_raw if isinstance(declared_raw, list) else [declared_raw])
                if _norm(d) not in ("", "none", "nil", "no", "na", "not_applicable")]
    declared_tokens = [_condition_tokens(d) for d in declared]

    steps = [f"Declared on the form: {', '.join(map(str, declared)) or 'none'}."]
    undeclared = []
    for cond in found:
        name = cond.get("condition")
        tokens = _condition_tokens(name)
        matched = any(tokens & dt for dt in declared_tokens) if tokens else False
        evidence = cond.get("evidence") or cond.get("since") or ""
        steps.append(f"Found in records: {name}{f' ({evidence})' if evidence else ''} - "
                     f"{'declared' if matched else 'NOT declared'}.")
        if not matched:
            undeclared.append(str(name))

    inputs = {"conditions_found": found, "declared_conditions": declared}
    if undeclared:
        return _finding(rule, FindingStatus.NEEDS_REVIEW, inputs, steps,
                        f"The medical records mention {', '.join(undeclared)}, which the patient did not declare. "
                        "The policy says this must go to a doctor for medical review before the claim is settled.")
    return _finding(rule, FindingStatus.SATISFIED, inputs, steps,
                    "Every condition found in the medical records was declared by the patient.", Decimal("0"))


EVALUATORS = {
    "pre_hospitalization": evaluate_pre_hospitalization,
    "post_hospitalization": evaluate_post_hospitalization,
    "ambulance_limit": evaluate_ambulance_limit,
    "sum_insured": evaluate_sum_insured,
    "cashless_preauthorization": evaluate_cashless_preauthorization,
    "pre_existing_disclosure": evaluate_pre_existing_disclosure,
}
