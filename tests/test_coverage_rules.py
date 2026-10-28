"""Tests for the v3 coverage rules on policy page 5 (pre/post-hospitalisation, ambulance,
sum insured, cashless pre-authorisation, disclosure of existing conditions)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine import evaluate
from src.schemas import ClaimFact, Rulebook, SourceRef

ROOT = Path(__file__).resolve().parent.parent
SRC = SourceRef(document="hospital_bill.pdf", page=1, quote="x")


@pytest.fixture(scope="module")
def gold() -> Rulebook:
    return Rulebook.model_validate(json.loads((ROOT / "data" / "gold_rulebook.json").read_text(encoding="utf-8")))


def _run(gold, rule_id, facts):
    findings = evaluate(gold, [ClaimFact(name=n, value=v, source=SRC) for n, v in facts.items()], rule_ids=[rule_id])
    f = next(x for x in findings if x.rule_id == rule_id)
    return str(getattr(f.status, "value", f.status)), f


HOSP = {"admission_date": "2026-03-10", "discharge_date": "2026-03-14", "cause": "illness"}


# ---------------------------------------------------------------- pre-hospitalisation
def test_pre_hospitalization_inside_window(gold):
    st, f = _run(gold, "RULE_PRE_HOSPITALIZATION", {"hospitalization_details": HOSP, "pre_hospitalization_expenses": [
        {"description": "Blood tests", "amount": 1800, "date": "2026-02-20"}]})
    assert st == "satisfied" and f.estimated_impact == 0.0


def test_pre_hospitalization_outside_window_is_deducted(gold):
    st, f = _run(gold, "RULE_PRE_HOSPITALIZATION", {"hospitalization_details": HOSP, "pre_hospitalization_expenses": [
        {"description": "MRI scan", "amount": 6500, "date": "2026-01-20"},
        {"description": "Consultation", "amount": 800, "date": "2026-03-01"}]})
    assert st == "violated" and f.estimated_impact == 6500.0
    assert "MRI scan" in f.explanation and "Billed more than 30 days" in f.explanation


def test_pre_hospitalization_day_30_is_inside(gold):
    st, _ = _run(gold, "RULE_PRE_HOSPITALIZATION", {"hospitalization_details": HOSP, "pre_hospitalization_expenses": [
        {"description": "X-ray", "amount": 500, "date": "2026-02-08"}]})  # exactly 30 days before
    assert st == "satisfied"


def test_pre_hospitalization_without_admission_date(gold):
    st, _ = _run(gold, "RULE_PRE_HOSPITALIZATION", {"pre_hospitalization_expenses": [
        {"description": "X-ray", "amount": 500, "date": "2026-02-08"}]})
    assert st == "unable_to_verify"


def test_pre_hospitalization_undated_item_needs_review(gold):
    st, _ = _run(gold, "RULE_PRE_HOSPITALIZATION", {"hospitalization_details": HOSP, "pre_hospitalization_expenses": [
        {"description": "Pharmacy", "amount": 900, "date": None}]})
    assert st == "needs_review"


# ---------------------------------------------------------------- post-hospitalisation
def test_post_hospitalization_outside_window_is_deducted(gold):
    st, f = _run(gold, "RULE_POST_HOSPITALIZATION", {"hospitalization_details": HOSP, "post_hospitalization_expenses": [
        {"description": "Follow-up physiotherapy", "amount": 4000, "date": "2026-05-20"},
        {"description": "Follow-up consultation", "amount": 700, "date": "2026-04-01"}]})
    assert st == "violated" and f.estimated_impact == 4000.0


def test_post_hospitalization_needs_discharge_date(gold):
    st, _ = _run(gold, "RULE_POST_HOSPITALIZATION", {
        "hospitalization_details": {"admission_date": "2026-03-10"},
        "post_hospitalization_expenses": [{"description": "Medicines", "amount": 700, "date": "2026-04-01"}]})
    assert st == "unable_to_verify"


# ---------------------------------------------------------------- ambulance
def test_ambulance_over_limit(gold):
    st, f = _run(gold, "RULE_AMBULANCE_LIMIT", {"ambulance_charges": {"amount": 3500}})
    assert st == "violated" and f.estimated_impact == 1500.0


def test_ambulance_within_limit(gold):
    st, _ = _run(gold, "RULE_AMBULANCE_LIMIT", {"ambulance_charges": {"amount": 1500}})
    assert st == "satisfied"


def test_ambulance_multiple_trips_are_added(gold):
    st, f = _run(gold, "RULE_AMBULANCE_LIMIT", {"ambulance_charges": [{"amount": 1200}, {"amount": 1300}]})
    assert st == "violated" and f.estimated_impact == 500.0


# ---------------------------------------------------------------- sum insured
def test_sum_insured_exceeded_with_previous_claims(gold):
    st, f = _run(gold, "RULE_SUM_INSURED", {"claim_amount_summary": {"total_claimed": 180000, "previously_paid_this_year": 350000}})
    assert st == "violated" and f.estimated_impact == 30000.0


def test_sum_insured_within(gold):
    st, _ = _run(gold, "RULE_SUM_INSURED", {"claim_amount_summary": {"total_claimed": 65000}})
    assert st == "satisfied"


# ---------------------------------------------------------------- cashless pre-authorisation
def _preauth(**kw):
    base = {"hospitalization_type": "planned", "documents_present": ["preauthorization_form", "cost_estimate"],
            "request_datetime": "2026-03-05T10:00", "admission_datetime": "2026-03-10T09:00"}
    base.update(kw)
    return {"preauthorization_request": base}


def test_cashless_planned_in_time(gold):
    assert _run(gold, "RULE_CASHLESS_PREAUTH", _preauth())[0] == "satisfied"


def test_cashless_planned_too_late(gold):
    st, f = _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(request_datetime="2026-03-08T10:00"))
    assert st == "violated" and "47" in f.explanation


def test_cashless_missing_cost_estimate(gold):
    st, f = _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(documents_present=["pre-authorisation form"]))
    assert st == "violated" and "cost estimate" in f.explanation


def test_cashless_emergency_within_24h(gold):
    st, _ = _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(hospitalization_type="emergency",
                 admission_datetime="2026-03-10T22:00", request_datetime="2026-03-11T08:00"))
    assert st == "satisfied"


def test_cashless_emergency_late(gold):
    st, _ = _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(hospitalization_type="emergency",
                 admission_datetime="2026-03-10T22:00", request_datetime="2026-03-12T08:00"))
    assert st == "violated"


@pytest.mark.parametrize("req,adm,expected", [
    ("2026-03-05", "2026-03-10", "satisfied"),     # 5 days: always at least 96 hours
    ("2026-03-07", "2026-03-10", "needs_review"),  # 3 days: 48-96 hours, cannot tell
    ("2026-03-09", "2026-03-10", "violated"),      # 1 day: at most 48 hours
])
def test_cashless_planned_dates_without_times(gold, req, adm, expected):
    st, _ = _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(request_datetime=req, admission_datetime=adm))
    assert st == expected


@pytest.mark.parametrize("adm,req,expected", [
    ("2026-03-10", "2026-03-10", "satisfied"),     # same day: under 24 hours
    ("2026-03-10", "2026-03-11", "needs_review"),  # next day: 0-48 hours
    ("2026-03-10", "2026-03-12", "violated"),      # two days later: over 24 hours
])
def test_cashless_emergency_dates_without_times(gold, adm, req, expected):
    st, _ = _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(hospitalization_type="emergency",
                 request_datetime=req, admission_datetime=adm))
    assert st == expected


def test_cashless_unknown_admission_type_needs_review(gold):
    assert _run(gold, "RULE_CASHLESS_PREAUTH", _preauth(hospitalization_type=None))[0] == "needs_review"


# ---------------------------------------------------------------- disclosure of existing conditions
def test_undeclared_condition_goes_to_medical_review(gold):
    st, f = _run(gold, "RULE_PRE_EXISTING_DISCLOSURE", {
        "declared_conditions": {"conditions": []},
        "medical_conditions_found": [{"condition": "Type 2 diabetes mellitus", "evidence": "on metformin for 6 years"}]})
    assert st == "needs_review" and "Type 2 diabetes mellitus" in f.explanation


def test_declared_condition_matches_different_wording(gold):
    st, _ = _run(gold, "RULE_PRE_EXISTING_DISCLOSURE", {
        "declared_conditions": {"conditions": ["Diabetes"]},
        "medical_conditions_found": [{"condition": "Type 2 diabetes mellitus"}]})
    assert st == "satisfied"


def test_declared_none_but_hypertension_found(gold):
    st, _ = _run(gold, "RULE_PRE_EXISTING_DISCLOSURE", {
        "declared_conditions": {"conditions": ["None"]},
        "medical_conditions_found": [{"condition": "Essential hypertension"}]})
    assert st == "needs_review"


def test_no_conditions_found_is_satisfied(gold):
    assert _run(gold, "RULE_PRE_EXISTING_DISCLOSURE", {"declared_conditions": {"conditions": []}})[0] == "satisfied"


def test_conditions_found_but_no_declaration_available(gold):
    st, _ = _run(gold, "RULE_PRE_EXISTING_DISCLOSURE", {
        "medical_conditions_found": [{"condition": "Asthma"}]})
    assert st == "unable_to_verify"


def test_no_condition_information_at_all_is_unable_to_verify(gold):
    assert _run(gold, "RULE_PRE_EXISTING_DISCLOSURE", {"ambulance_charges": {"amount": 100}})[0] == "unable_to_verify"
