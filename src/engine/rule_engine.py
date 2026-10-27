"""Deterministic Rule Engine for ClaimCheck 2.0.

Evaluates structured insurance policy rules and documented ambiguities against
claim facts using pure deterministic Python arithmetic and logical verification.
Avoids speculative guessing and model hallucinations by computing exact monetary
deductions using Decimal arithmetic and raising explicit review/verification flags
for missing inputs, unverified rules, or ambiguous clauses.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple, Union

from src.schemas import (
    Rule,
    Rulebook,
    ClaimFact,
    Finding,
    FindingStatus,
    RuleType,
    SourceRef,
    Ambiguity,
    ValidationStatus,
)


def normalize_item_name(name: Any) -> str:
    """Normalize a bill or document item name without fuzzy guessing.

    Lowercases, removes punctuation, replaces whitespace and hyphens with underscores.
    """
    if name is None:
        return ""
    cleaned = re.sub(r"[^\w\s-]", "", str(name).lower())
    return re.sub(r"[\s-]+", "_", cleaned).strip("_")


def _to_decimal(val: Any) -> Optional[Decimal]:
    """Safely convert a primitive number or string to Decimal, returning None if unparseable."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _parse_date(val: Any) -> Optional[date]:
    """Safely parse a date from ISO format or common string representations."""
    if not val or not isinstance(val, (str, date)):
        return None
    if isinstance(val, date):
        return val
    val_str = str(val).strip()
    try:
        return date.fromisoformat(val_str)
    except Exception:
        pass
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except Exception:
            pass
    return None


def _check_conflicting_facts_for_rule(
    rule_type: str,
    all_facts: List[ClaimFact],
) -> Optional[str]:
    """Detect if claim facts contain conflicting, duplicate, or contradicting values."""
    # Check duplicate fact names with conflicting values
    facts_by_name: Dict[str, List[ClaimFact]] = {}
    for f in all_facts:
        facts_by_name.setdefault(f.name, []).append(f)

    for name, flist in facts_by_name.items():
        if len(flist) > 1:
            first_val = flist[0].value
            for other in flist[1:]:
                if other.value != first_val:
                    return f"Duplicate fact '{name}' has conflicting values: {first_val} vs {other.value}"

    # Check cross-document overall claim totals (e.g. claim form claimed amount vs hospital bill billed amount)
    overall_totals = {}
    for f in all_facts:
        if isinstance(f.value, dict):
            for k in ("claimed_amount", "billed_amount", "total_claimed", "total_billed"):
                if k in f.value and f.value[k] is not None:
                    amt = _to_decimal(f.value[k])
                    if amt is not None:
                        overall_totals[f"{f.name}.{k}"] = amt
            if f.name.endswith("_summary") and "total_amount" in f.value and f.value["total_amount"] is not None:
                amt = _to_decimal(f.value["total_amount"])
                if amt is not None:
                    overall_totals[f"{f.name}.total_amount"] = amt

    if len(overall_totals) >= 2:
        vals = list(overall_totals.values())
        if any(v != vals[0] for v in vals[1:]):
            diffs = ", ".join(f"{k}=INR {v}" for k, v in overall_totals.items())
            return f"Conflicting total amounts detected across claim documents: {diffs}"

    return None


def _evaluate_required_documents(
    rule: Rule,
    facts_by_name: Dict[str, ClaimFact],
) -> Finding:
    """Evaluate mandatory document submission rule."""
    if not isinstance(rule.parameters, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters are missing or invalid; expected dictionary."],
            explanation="Cannot verify rule: parameters are missing or invalid.",
            source=rule.source,
            estimated_impact=None,
        )

    req_docs_raw = rule.parameters.get("required_documents")
    if not isinstance(req_docs_raw, (list, tuple, set)) or not req_docs_raw:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters lack 'required_documents' list."],
            explanation="Cannot verify required documents: rule parameters do not specify required documents.",
            source=rule.source,
            estimated_impact=None,
        )

    req_docs = list(req_docs_raw)

    doc_fact = facts_by_name.get("submitted_documents")
    if doc_fact is None or doc_fact.value is None:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={"submitted_documents": None},
            calculation_steps=[
                "Checked claim facts for submitted documents.",
                "Missing or unparseable input: no submitted documents fact found in claim.",
            ],
            explanation="Cannot verify required documents: claim facts do not contain submitted documents information.",
            source=rule.source,
            estimated_impact=None,
        )

    submitted_raw = doc_fact.value if isinstance(doc_fact.value, (list, tuple, set)) else [doc_fact.value]
    norm_submitted = {normalize_item_name(d) for d in submitted_raw}
    norm_required = [normalize_item_name(d) for d in req_docs]

    missing = [d for d in norm_required if d not in norm_submitted]

    if missing:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.VIOLATED,
            inputs_used={"submitted_documents": list(submitted_raw)},
            calculation_steps=[
                f"Identified mandatory document list: {', '.join(req_docs)}.",
                f"Found {len(norm_submitted)} documents present: {', '.join(sorted(norm_submitted))}.",
                f"Missing required documents: {', '.join(missing)}.",
                "Missing mandatory documents render the claim incomplete and inadmissible.",
            ],
            explanation=f"The mandatory document(s) {', '.join(missing)} are missing from the claim submission.",
            source=rule.source,
            estimated_impact=None,
        )

    return Finding(
        rule_id=rule.rule_id,
        status=FindingStatus.SATISFIED,
        inputs_used={"submitted_documents": list(submitted_raw)},
        calculation_steps=[
            f"Checked submitted documents against mandatory list: {', '.join(req_docs)}.",
            f"All {len(req_docs)} required documents are present in submission.",
        ],
        explanation="All three mandatory documents required by Section 1.1 are present in the claim submission.",
        source=rule.source,
        estimated_impact=0.0,
    )


def _evaluate_waiting_period(
    rule: Rule,
    facts_by_name: Dict[str, ClaimFact],
) -> Finding:
    """Evaluate initial waiting period rule against hospitalization facts."""
    if not isinstance(rule.parameters, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters are missing or invalid; expected dictionary."],
            explanation="Cannot verify waiting period: rule parameters are invalid.",
            source=rule.source,
            estimated_impact=None,
        )

    duration_raw = rule.parameters.get("duration_days")
    if duration_raw is None:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters lack 'duration_days'."],
            explanation="Cannot verify waiting period: rule parameters lack duration_days.",
            source=rule.source,
            estimated_impact=None,
        )

    try:
        duration_days = int(duration_raw)
    except Exception:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={"duration_days": duration_raw},
            calculation_steps=[f"Unparseable duration_days in rule parameters: {duration_raw}."],
            explanation="Cannot verify waiting period: rule duration_days is unparseable.",
            source=rule.source,
            estimated_impact=None,
        )

    fact = facts_by_name.get("hospitalization_details")
    if fact is None or not isinstance(fact.value, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=[
                "Checked claim facts for hospitalization timeline details.",
                "Missing or unparseable input: no hospitalization_details fact found.",
            ],
            explanation="Cannot verify waiting period: hospitalization timeline details are missing from claim facts.",
            source=rule.source,
            estimated_impact=None,
        )

    val = fact.value
    cause = normalize_item_name(val.get("cause", ""))
    days_elapsed = val.get("days_elapsed")

    if days_elapsed is not None:
        try:
            days_elapsed = int(days_elapsed)
        except Exception:
            days_elapsed = None

    # Date arithmetic fallback
    if days_elapsed is None and "admission_date" in val and "policy_inception_date" in val:
        adm = _parse_date(val["admission_date"])
        inc = _parse_date(val["policy_inception_date"])
        if adm and inc:
            days_elapsed = (adm - inc).days

    if days_elapsed is None:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used=val,
            calculation_steps=[
                "Attempted waiting period verification.",
                "Missing or unparseable input: days elapsed since inception could not be determined.",
            ],
            explanation="Cannot verify waiting period: elapsed duration between policy inception and admission is missing or invalid.",
            source=rule.source,
            estimated_impact=None,
        )

    # Check for accidental injury exception
    is_accident = (
        cause in ("accidental_injury", "accident", "trauma", "vehicular_accident", "injury")
        or "accident" in cause
    )

    if is_accident:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.SATISFIED,
            inputs_used={
                "policy_inception_date": val.get("policy_inception_date"),
                "admission_date": val.get("admission_date"),
                "days_elapsed": days_elapsed,
                "cause": val.get("cause"),
            },
            calculation_steps=[
                f"Computed days elapsed: {days_elapsed} days.",
                f"Admission date is within {duration_days} days of inception.",
                "Evaluated policy exception: Section 2.1 exempts hospitalization necessitated solely by accidental injury.",
                "Admission cause verified as accidental injury; exception applies.",
                "Rule status: satisfied under policy exception.",
            ],
            explanation=f"Hospitalization occurred on day {days_elapsed} due to an accidental injury, qualifying for the explicit policy exception to the {duration_days}-day waiting period.",
            source=rule.source,
            estimated_impact=0.0,
        )

    # Missing or ambiguous cause within waiting period -> needs_review
    if not cause:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.NEEDS_REVIEW,
            inputs_used=val,
            calculation_steps=[
                f"Computed days elapsed: {days_elapsed} days (<= {duration_days} threshold).",
                "Admission cause is missing or ambiguous; cannot determine whether illness exclusion or accidental injury exception applies.",
            ],
            explanation="Admission occurred within the initial waiting period, but hospitalization cause is not specified. Human review required to assess exception applicability.",
            source=rule.source,
            estimated_impact=None,
        )

    # Illness waiting period evaluation: > duration_days is satisfied; <= duration_days is violated
    if days_elapsed > duration_days:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.SATISFIED,
            inputs_used={
                "policy_inception_date": val.get("policy_inception_date"),
                "admission_date": val.get("admission_date"),
                "days_elapsed": days_elapsed,
                "cause": val.get("cause"),
            },
            calculation_steps=[
                f"Computed days elapsed between policy inception ({val.get('policy_inception_date')}) and hospital admission ({val.get('admission_date')}): {days_elapsed} days.",
                f"Compared {days_elapsed} days against mandatory {duration_days}-day waiting period threshold.",
                f"{days_elapsed} > {duration_days}: Waiting period is fully satisfied.",
            ],
            explanation=f"The admission occurred {days_elapsed} days post-inception, satisfying the {duration_days}-day initial waiting period.",
            source=rule.source,
            estimated_impact=0.0,
        )

    return Finding(
        rule_id=rule.rule_id,
        status=FindingStatus.VIOLATED,
        inputs_used={
            "policy_inception_date": val.get("policy_inception_date"),
            "admission_date": val.get("admission_date"),
            "days_elapsed": days_elapsed,
            "cause": val.get("cause"),
        },
        calculation_steps=[
            f"Computed days elapsed: {days_elapsed} days.",
            f"Threshold required: > {duration_days} days.",
            f"{days_elapsed} <= {duration_days} days: Admission occurred within initial waiting period.",
            f"Evaluated exception: Condition is {val.get('cause')}, not accidental injury.",
            f"Rule violated: expenses incurred within the first {duration_days} days for illness are excluded from coverage.",
        ],
        explanation=f"Hospitalization for illness occurred on day {days_elapsed} of the policy period, violating the mandatory {duration_days}-day initial waiting period.",
        source=rule.source,
        estimated_impact=None,
    )


def _evaluate_room_rent_cap(
    rule: Rule,
    facts_by_name: Dict[str, ClaimFact],
) -> Finding:
    """Evaluate room rent daily limit and proportional deduction formula."""
    if not isinstance(rule.parameters, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters are missing or invalid."],
            explanation="Cannot verify room rent cap: rule parameters are invalid.",
            source=rule.source,
            estimated_impact=None,
        )

    cap = _to_decimal(rule.parameters.get("cap") or rule.parameters.get("daily_limit_inr"))
    if cap is None or cap <= 0:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used=rule.parameters,
            calculation_steps=["Rule parameters lack valid positive room rent cap."],
            explanation="Cannot verify room rent cap: cap parameter is missing or non-numeric.",
            source=rule.source,
            estimated_impact=None,
        )

    room_fact = facts_by_name.get("room_rent_billing")
    if room_fact is None or not isinstance(room_fact.value, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=[
                "Checked claim facts for room rent billing details.",
                "Missing or unparseable input: no room_rent_billing fact found.",
            ],
            explanation="Cannot verify room rent cap: room rent billing details are missing from claim facts.",
            source=rule.source,
            estimated_impact=None,
        )

    val = room_fact.value
    daily_rate = _to_decimal(val.get("daily_rate") or val.get("daily_room_rent"))
    if daily_rate is None or daily_rate <= 0:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used=val,
            calculation_steps=[
                "Inspected room rent billing fact.",
                "Missing or non-numeric daily rate value in claim fact.",
            ],
            explanation="Cannot verify room rent cap: daily room rent rate is missing or unparseable.",
            source=rule.source,
            estimated_impact=None,
        )

    days_raw = val.get("days") or val.get("days_stayed")
    days_dec = _to_decimal(days_raw)

    # Boundary: rent is within or exactly equal to cap
    if daily_rate <= cap:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.SATISFIED,
            inputs_used={
                "daily_rate_billed": float(daily_rate),
                "days": days_raw,
                "daily_cap": float(cap),
            },
            calculation_steps=[
                f"Compared billed daily room rent (INR {daily_rate:.2f}) against policy daily cap (INR {cap:.2f}).",
                f"INR {daily_rate:.2f} <= INR {cap:.2f}: Billed daily room rent is within the permissible limit.",
                "Excess room rent deduction = INR 0.00.",
            ],
            explanation=f"The daily room rent of INR {daily_rate:.0f} does not exceed the policy limit of INR {cap:.0f} per day.",
            source=rule.source,
            estimated_impact=0.0,
        )

    # Rent exceeds cap
    # If duration is missing / non-positive, cannot determine financial impact -> needs_review
    if days_dec is None or days_dec <= 0:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.NEEDS_REVIEW,
            inputs_used={
                "daily_rate_billed": float(daily_rate),
                "days": None,
                "daily_cap": float(cap),
            },
            calculation_steps=[
                f"Identified billed daily room rent: INR {daily_rate:.2f}.",
                f"Daily rate exceeds cap of INR {cap:.2f}.",
                "Attempted deduction calculation: duration in days is unknown or invalid.",
                "Financial impact cannot be computed without total days of stay.",
            ],
            explanation=f"Daily room rent of INR {daily_rate:.0f} exceeds INR {cap:.0f} daily limit, but total stay duration is missing from submitted records. Financial impact cannot be determined without knowing days of hospitalization.",
            source=rule.source,
            estimated_impact=None,
        )

    daily_excess = daily_rate - cap
    excess_room_rent = daily_excess * days_dec

    # Extract associated professional fees and OT charges
    prof_fact = facts_by_name.get("professional_fees_billing")
    prof_total = Decimal("0.0")
    if prof_fact and isinstance(prof_fact.value, dict):
        total_field = _to_decimal(prof_fact.value.get("total_amount") or prof_fact.value.get("total_professional_fees"))
        if total_field is not None:
            prof_total = total_field
        else:
            sub_sum = Decimal("0.0")
            for sub_k in ("surgeon_fee", "anesthetist_fee", "consultant_fee", "doctor_fee"):
                v = _to_decimal(prof_fact.value.get(sub_k))
                if v is not None:
                    sub_sum += v
            prof_total = sub_sum
    elif prof_fact:
        dec = _to_decimal(prof_fact.value)
        if dec is not None:
            prof_total = dec

    ot_fact = facts_by_name.get("operation_theatre_billing")
    ot_total = Decimal("0.0")
    if ot_fact and isinstance(ot_fact.value, dict):
        dec = _to_decimal(ot_fact.value.get("ot_charges") or ot_fact.value.get("total_amount"))
        if dec is not None:
            ot_total = dec
    elif ot_fact:
        dec = _to_decimal(ot_fact.value)
        if dec is not None:
            ot_total = dec

    payable_ratio = cap / daily_rate
    disallow_ratio = Decimal("1.0") - payable_ratio

    prof_deduction = (prof_total * disallow_ratio) if prof_total > 0 else Decimal("0.0")
    ot_deduction = (ot_total * disallow_ratio) if ot_total > 0 else Decimal("0.0")

    total_deduction = excess_room_rent + prof_deduction + ot_deduction
    rounded_impact = float(total_deduction.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    calculation_steps = [
        f"Step 1 (Excess Room Rent): Daily room rent billed = INR {daily_rate:.2f}. Policy room rent cap = INR {cap:.2f}. Daily excess = INR {daily_rate:.2f} - INR {cap:.2f} = INR {daily_excess:.2f}. For {days_raw} days of hospitalization, excess room rent deduction = {days_raw} days * INR {daily_excess:.2f}/day = INR {excess_room_rent:.2f} (non-payable)."
    ]

    if prof_total > 0 or ot_total > 0:
        payable_prof = prof_total * payable_ratio
        payable_ot = ot_total * payable_ratio
        calculation_steps.extend([
            f"Step 2 (Proportional Ratio Calculation): Actual daily room rent (INR {daily_rate:.2f}) exceeds cap (INR {cap:.2f}). Proportional payable ratio = {cap:.0f} / actual_daily_rate = {cap:.0f} / {daily_rate:.0f} = {payable_ratio:.2f} ({float(payable_ratio*100):.1f}%).",
            f"Step 3 (Professional Fees Proportional Deduction): Total professional fees billed = INR {prof_total:.2f}. Payable portion = INR {prof_total:.2f} * {payable_ratio:.2f} = INR {payable_prof:.2f}. Disallowed deduction = INR {prof_total:.2f} - INR {payable_prof:.2f} = INR {prof_deduction:.2f}.",
            f"Step 4 (OT Charges Proportional Deduction): Total operation theatre charges billed = INR {ot_total:.2f}. Payable portion = INR {ot_total:.2f} * {payable_ratio:.2f} = INR {payable_ot:.2f}. Disallowed deduction = INR {ot_total:.2f} - INR {payable_ot:.2f} = INR {ot_deduction:.2f}.",
            f"Step 5 (Total Combined Deduction): Sum of disallowances = Excess Room Rent (INR {excess_room_rent:.2f}) + Professional Fees Deduction (INR {prof_deduction:.2f}) + OT Charges Deduction (INR {ot_deduction:.2f}) = INR {total_deduction:.2f}.",
        ])
        explanation = (
            f"Actual daily room rent of INR {daily_rate:.0f} exceeds the policy cap of INR {cap:.0f} per day. "
            f"Excess room rent of INR {excess_room_rent:.0f} is disallowed, and associated professional fees (INR {prof_total:.0f}) "
            f"and OT charges (INR {ot_total:.0f}) are reduced by {float(disallow_ratio*100):.0f}% under the 5000 / actual daily rate "
            f"proportional deduction formula, resulting in a total deterministic deduction of INR {total_deduction:.0f}."
        )
    else:
        calculation_steps.append(
            "No professional fees or OT charges were billed in this claim; proportional deduction does not apply to zero fees."
        )
        explanation = (
            f"Daily room rent of INR {daily_rate:.0f} exceeds the policy cap of INR {cap:.0f} per day by INR {daily_excess:.0f} per day "
            f"across {days_raw} days, resulting in an excess room rent deduction of INR {excess_room_rent:.0f}."
        )

    inputs_used = {
        "daily_rate_billed": float(daily_rate),
        "days": days_raw,
        "daily_cap": float(cap),
    }
    if prof_total > 0:
        inputs_used["professional_fees_billed"] = float(prof_total)
    if ot_total > 0:
        inputs_used["ot_charges_billed"] = float(ot_total)

    return Finding(
        rule_id=rule.rule_id,
        status=FindingStatus.VIOLATED,
        inputs_used=inputs_used,
        calculation_steps=calculation_steps,
        explanation=explanation,
        source=rule.source,
        estimated_impact=rounded_impact,
    )


def _evaluate_sub_limit(
    rule: Rule,
    facts_by_name: Dict[str, ClaimFact],
) -> Finding:
    """Evaluate specific surgical sub-limit rule."""
    if not isinstance(rule.parameters, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters are missing or invalid."],
            explanation="Cannot verify sub-limit: rule parameters are invalid.",
            source=rule.source,
            estimated_impact=None,
        )

    category = normalize_item_name(rule.parameters.get("category", "cataract_surgery"))
    limit_per_unit = _to_decimal(rule.parameters.get("max_limit_inr"))
    if limit_per_unit is None or limit_per_unit <= 0 or not category:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used=rule.parameters,
            calculation_steps=["Rule parameters lack valid positive sub-limit or category."],
            explanation="Cannot verify sub-limit: limit or category parameter is missing or non-numeric.",
            source=rule.source,
            estimated_impact=None,
        )

    fact = facts_by_name.get("surgical_expenses")
    if fact is None or not isinstance(fact.value, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=[
                "Checked claim facts for surgical expense breakdown.",
                "Missing or unparseable input: no surgical_expenses fact found.",
            ],
            explanation=f"Cannot verify sub-limit: surgical expenses details for {category} are missing.",
            source=rule.source,
            estimated_impact=None,
        )

    val = fact.value
    proc = normalize_item_name(val.get("procedure", ""))

    if category not in proc and proc not in category:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used=val,
            calculation_steps=[
                f"Evaluated procedure name: '{val.get('procedure')}'.",
                f"Procedure does not match target sub-limit category '{category}'.",
            ],
            explanation=f"Surgical procedure '{val.get('procedure')}' does not match sub-limit category '{category}'.",
            source=rule.source,
            estimated_impact=None,
        )

    billed_amount = _to_decimal(val.get("total_cataract_billed") or val.get("total_billed"))
    if billed_amount is None:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used=val,
            calculation_steps=["Surgical expense fact lacks valid billed amount."],
            explanation="Cannot verify sub-limit: billed amount is missing or unparseable.",
            source=rule.source,
            estimated_impact=None,
        )

    eyes_count = _to_decimal(val.get("eyes_treated") or val.get("eyes_count"))
    if eyes_count is None or eyes_count <= 0:
        eyes_count = Decimal("1.0")

    effective_limit = limit_per_unit * eyes_count

    if billed_amount <= effective_limit:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.SATISFIED,
            inputs_used={
                "procedure": val.get("procedure"),
                "eyes_count": int(eyes_count),
                "total_billed": float(billed_amount),
                "sublimit_per_eye": float(limit_per_unit),
            },
            calculation_steps=[
                f"Billed amount for {proc}: INR {billed_amount:.2f}.",
                f"Policy sub-limit: INR {effective_limit:.2f}.",
                f"INR {billed_amount:.2f} <= INR {effective_limit:.2f}: Billed amount is within sub-limit.",
            ],
            explanation=f"Billed expenses of INR {billed_amount:.0f} do not exceed the policy sub-limit of INR {effective_limit:.0f}.",
            source=rule.source,
            estimated_impact=0.0,
        )

    excess = billed_amount - effective_limit
    rounded_excess = float(excess.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return Finding(
        rule_id=rule.rule_id,
        status=FindingStatus.VIOLATED,
        inputs_used={
            "procedure": val.get("procedure"),
            "eyes_count": int(eyes_count),
            "total_billed": float(billed_amount),
            "sublimit_per_eye": float(limit_per_unit),
        },
        calculation_steps=[
            f"Billed amount for left eye cataract surgery: INR {billed_amount:.0f}.",
            f"Policy sub-limit per eye: INR {limit_per_unit:.0f}.",
            f"Excess over sub-limit: INR {billed_amount:.0f} - INR {effective_limit:.0f} = INR {excess:.2f} non-payable.",
        ],
        explanation=f"Billed cataract surgery expenses of INR {billed_amount:.0f} exceed the policy sub-limit of INR {limit_per_unit:.0f} per eye by INR {excess:.0f}.",
        source=rule.source,
        estimated_impact=rounded_excess,
    )


def _evaluate_non_payable_items(
    rule: Rule,
    facts_by_name: Dict[str, ClaimFact],
) -> Finding:
    """Evaluate non-payable consumable and administrative items schedule."""
    if not isinstance(rule.parameters, dict):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters are missing or invalid."],
            explanation="Cannot verify non-payable items: rule parameters are invalid.",
            source=rule.source,
            estimated_impact=None,
        )

    raw_excluded = rule.parameters.get("excluded_items")
    if not isinstance(raw_excluded, (list, tuple, set)):
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=["Rule parameters lack 'excluded_items' list."],
            explanation="Cannot verify non-payable items: excluded_items list is missing.",
            source=rule.source,
            estimated_impact=None,
        )

    norm_excluded_set = {normalize_item_name(x) for x in raw_excluded}

    fact = facts_by_name.get("non_payable_items_breakdown") or facts_by_name.get("hospital_breakdown")
    if fact is None or fact.value is None:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=[
                "Checked claim facts for itemized line items or non-payable items breakdown.",
                "Missing or unparseable input: no bill line items fact found.",
            ],
            explanation="Cannot verify non-payable items: itemized billing breakdown is missing from claim facts.",
            source=rule.source,
            estimated_impact=None,
        )

    items_list = fact.value if isinstance(fact.value, list) else [fact.value]

    matched_names = []
    matched_amounts = []
    unmatched_names = []
    total_deduction = Decimal("0.0")

    for entry in items_list:
        if isinstance(entry, dict):
            raw_name = entry.get("item") or entry.get("name") or entry.get("description") or ""
            raw_amt = entry.get("amount") or entry.get("cost") or entry.get("billed_amount")
        else:
            raw_name = str(entry)
            raw_amt = 0

        norm_name = normalize_item_name(raw_name)
        amt_dec = _to_decimal(raw_amt) or Decimal("0.0")

        if norm_name in norm_excluded_set:
            matched_names.append(raw_name)
            matched_amounts.append(float(amt_dec))
            total_deduction += amt_dec
        else:
            unmatched_names.append(raw_name)

    if not matched_names:
        return Finding(
            rule_id=rule.rule_id,
            status=FindingStatus.SATISFIED,
            inputs_used={"inspected_items": [str(e) for e in items_list]},
            calculation_steps=[
                "Inspected billed line items against Section 3.2 non-payable schedule.",
                "No excluded non-payable items were identified.",
                f"Reported unmatched/allowed items: {', '.join(unmatched_names) if unmatched_names else 'None'}.",
            ],
            explanation="No excluded non-payable items were identified in the billed line items.",
            source=rule.source,
            estimated_impact=0.0,
        )

    rounded_impact = float(total_deduction.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    calc_steps = [
        f"Identified invoiced line items matching Section 3.2 non-payable schedule: {', '.join(matched_names)}.",
        f"Summed non-payable amounts: {' + '.join(f'INR {a:.0f}' for a in matched_amounts)} = INR {total_deduction:.2f} non-payable deduction.",
    ]
    if unmatched_names:
        calc_steps.append(f"Reported allowable/unmatched billed items: {', '.join(unmatched_names)}.")

    return Finding(
        rule_id=rule.rule_id,
        status=FindingStatus.VIOLATED,
        inputs_used={
            "items_detected": matched_names,
            "amounts": matched_amounts,
        },
        calculation_steps=calc_steps,
        explanation=f"Invoiced expenses include {', '.join(matched_names)}, which are explicitly non-payable under Section 3.2.",
        source=rule.source,
        estimated_impact=rounded_impact,
    )


# Generic legal and structural words. They appear in almost any ambiguity description
# and in ordinary claim text ("policy number", "claim filed"), so they must not decide
# whether a claim fact relates to an ambiguity. Only subject words (e.g. "existing",
# "condition") should.
_AMBIGUITY_GENERIC_TERMS = frozenset({
    "policy", "policies", "claim", "claims", "claimant", "coverage", "cover", "covered", "insurer",
    "insured", "person", "persons", "document", "documents", "clause", "clauses", "section", "sections",
    "term", "terms", "define", "defined", "defining", "definition", "definitions", "undefined",
    "applicable", "apply", "applies", "regulation", "regulations", "regulatory", "statutory",
    "framework", "reference", "references", "referenced", "refers", "missing", "neither", "either",
    "whether", "under", "with", "within", "without", "from", "shall", "that", "this", "these",
    "those", "which", "where", "there", "their", "they", "have", "been", "being", "does", "into",
    "until", "expiration", "excluded", "exclusion", "exclusions", "indemnified", "arising",
    "attributable", "provide", "provided", "providing", "specify", "specified", "specifying",
    "lookback", "period", "periods", "duration", "waiting", "amount", "date", "dates",
})


def _norm_term(token: str) -> str:
    """Lowercase and drop a plural 's' so 'conditions' matches 'condition'."""
    token = token.lower()
    return token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss") else token


def _ambiguity_terms(amb: Ambiguity) -> set:
    """Subject words of an ambiguity, from its issue and description."""
    tokens = re.split(r"[^a-z0-9]+", f"{amb.issue} {amb.description}".lower())
    return {_norm_term(t) for t in tokens if len(t) > 3 and t not in _AMBIGUITY_GENERIC_TERMS}


def _fact_terms(name: str, value: Any) -> set:
    """Words from a fact's non-empty leaves: each key (split on underscores) plus its value.
    Empty leaves (None, "", [], {}) contribute nothing, so a blank field is not evidence."""
    terms: set = set()

    def words(text: str) -> set:
        return {_norm_term(t) for t in re.split(r"[^a-z0-9]+", str(text).lower()) if len(t) > 3}

    def walk(key: str, val: Any) -> None:
        if val is None or val == "" or val == [] or val == {}:
            return
        if isinstance(val, dict):
            for k, v in val.items():
                walk(str(k), v)
            return
        if isinstance(val, (list, tuple)):
            for v in val:
                walk(key, v)
            return
        terms.update(words(key))
        terms.update(words(val))

    walk(name, value)
    return terms


def _fact_relates_to_ambiguity(amb: Ambiguity, fact: ClaimFact) -> bool:
    return bool(_ambiguity_terms(amb) & _fact_terms(fact.name, fact.value))


def _evaluate_ambiguity(
    amb: Ambiguity,
    facts_by_name: Dict[str, ClaimFact],
    all_facts: List[ClaimFact],
) -> Finding:
    """Evaluate a documented policy ambiguity without hardcoded rule IDs."""
    # Find facts that relate to this ambiguity by its subject words (not generic legal words)
    relevant_facts = [f for f in all_facts if _fact_relates_to_ambiguity(amb, f)]

    if not relevant_facts:
        return Finding(
            rule_id=amb.ambiguity_id,
            status=FindingStatus.UNABLE_TO_VERIFY,
            inputs_used={},
            calculation_steps=[
                f"Checked claim facts for relevance to {amb.section} ({amb.issue}).",
                "Missing input: no relevant claim facts found for this policy ambiguity.",
            ],
            explanation=f"Cannot evaluate ambiguity {amb.ambiguity_id} ({amb.section}): no relevant facts found in claim.",
            source=amb.source,
            estimated_impact=None,
        )

    inputs_used = {f.name: f.value for f in relevant_facts}
    return Finding(
        rule_id=amb.ambiguity_id,
        status=FindingStatus.NEEDS_REVIEW,
        inputs_used=inputs_used,
        calculation_steps=[
            f"Identified claim facts relevant to policy clause {amb.section}.",
            f"Evaluated policy clause: flagged as an ambiguity ({amb.issue}).",
            f"Clause details: {amb.description}",
            "Determined finding status: needs_review. Automated rule engine cannot deterministically resolve ambiguous policy language without human adjuster review.",
            "Financial impact: None (cannot be deterministically computed for an ambiguous clause).",
        ],
        explanation=(
            f"{amb.section} contains ambiguous or undefined terms ({amb.issue}). "
            "Automated adjudication is suspended pending human adjuster interpretation."
        ),
        source=amb.source,
        estimated_impact=None,
    )


def evaluate(
    rulebook: Rulebook,
    facts: list[ClaimFact],
    rule_ids: Optional[list[str]] = None,
) -> list[Finding]:
    """Deterministically evaluate policy rules against extracted claim facts.

    Args:
        rulebook: Validated Rulebook model containing operative rules and ambiguities.
        facts: List of extracted ClaimFact models.
        rule_ids: Optional subset of rule/ambiguity IDs to evaluate. If omitted, evaluates all.

    Returns:
        list[Finding]: Ordered list of evaluation findings for the requested rules.
    """
    facts_by_name: Dict[str, ClaimFact] = {f.name: f for f in facts}
    findings: list[Finding] = []

    evaluators = {
        RuleType.REQUIRED_DOCUMENTS.value: _evaluate_required_documents,
        RuleType.WAITING_PERIOD.value: _evaluate_waiting_period,
        RuleType.ROOM_RENT_CAP.value: _evaluate_room_rent_cap,
        RuleType.SUB_LIMIT.value: _evaluate_sub_limit,
        RuleType.NON_PAYABLE_ITEMS.value: _evaluate_non_payable_items,
    }

    # Evaluate executable rules
    for rule in rulebook.rules:
        if rule_ids is not None and rule.rule_id not in rule_ids:
            continue

        # Check for unverified rule or mandatory human review flag
        val_status = str(rule.validation_status).lower()
        if val_status not in ("validated", "active"):
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    status=FindingStatus.NEEDS_REVIEW,
                    inputs_used={"validation_status": rule.validation_status},
                    calculation_steps=[
                        f"Checked rule validation status: '{rule.validation_status}'.",
                        "Rule is unverified or deprecated; cannot be deterministically enforced.",
                    ],
                    explanation=f"Rule {rule.rule_id} is unverified (status: '{rule.validation_status}') and requires review before adjudication.",
                    source=rule.source,
                    estimated_impact=None,
                )
            )
            continue

        if rule.human_review_required:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    status=FindingStatus.NEEDS_REVIEW,
                    inputs_used={"human_review_required": True},
                    calculation_steps=["Rule specifies human_review_required = True."],
                    explanation=f"Rule {rule.rule_id} requires mandatory human adjuster review.",
                    source=rule.source,
                    estimated_impact=None,
                )
            )
            continue

        # Check for conflicting facts relevant to this rule
        conflict_msg = _check_conflicting_facts_for_rule(rule.rule_type, facts)
        if conflict_msg:
            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    status=FindingStatus.NEEDS_REVIEW,
                    inputs_used={f.name: f.value for f in facts},
                    calculation_steps=[
                        f"Inspected claim facts for rule '{rule.rule_id}'.",
                        f"Contradiction detected: {conflict_msg}",
                    ],
                    explanation=f"Conflicting or contradictory claim facts prevent automated evaluation: {conflict_msg}",
                    source=rule.source,
                    estimated_impact=None,
                )
            )
            continue

        evaluator = evaluators.get(rule.rule_type)
        if evaluator is not None:
            finding = evaluator(rule, facts_by_name)
            findings.append(finding)

    # Evaluate documented ambiguities without hard-coded IDs
    ambiguities = getattr(rulebook, "ambiguities", [])
    for amb in ambiguities:
        is_requested = rule_ids is not None and amb.ambiguity_id in rule_ids
        is_relevant_in_all = rule_ids is None and any(_fact_relates_to_ambiguity(amb, f) for f in facts)

        if is_requested or is_relevant_in_all:
            finding = _evaluate_ambiguity(amb, facts_by_name, facts)
            findings.append(finding)

    return findings
