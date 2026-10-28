"""Tests for paperwork mistake checks (names, policy number, dates, arithmetic, claimed vs billed, duplicates)."""

from __future__ import annotations

import copy

from src.engine.document_checks import run_document_checks
from src.schemas import ClaimFact, SourceRef

SRC = SourceRef(document="claim_form.pdf", page=1, quote="x")
POLICY = "POL-AURA-2026-001"

GOOD_DOCS = [
    {"document": "claim_form.pdf", "document_type": "claim_form", "page": 1, "quote": "Patient: Priya Patel",
     "patient_name": "Mrs. Priya Patel", "policy_number": POLICY,
     "admission_date": "2026-03-10", "discharge_date": "2026-03-14"},
    {"document": "hospital_bill.pdf", "document_type": "hospital_bill", "page": 1, "quote": "Patient: Priya Patel",
     "patient_name": "Priya Patel", "policy_number": "POL AURA 2026 001",
     "admission_date": "2026-03-10", "discharge_date": "2026-03-14", "stated_total": 47400,
     "line_items": [{"item": "Room charges", "amount": 30000}, {"item": "Consultation", "amount": 6000},
                    {"item": "Pharmacy", "amount": 8400}, {"item": "Admission processing", "amount": 1200},
                    {"item": "Amenity kit", "amount": 1800}]},
    {"document": "discharge_summary.pdf", "document_type": "discharge_summary", "page": 1, "quote": "Patient: Priya Patel",
     "patient_name": "PRIYA PATEL", "admission_date": "2026-03-10", "discharge_date": "2026-03-14"},
]


def _checks(docs, claimed=47400, policy=POLICY):
    facts = [ClaimFact(name="document_details", value=docs, source=SRC)]
    if claimed is not None:
        facts.append(ClaimFact(name="claim_amount_summary", value={"total_claimed": claimed}, source=SRC))
    return {f.rule_id: f for f in run_document_checks(facts, policy)}


def _status(f):
    return str(getattr(f.status, "value", f.status))


def test_clean_documents_pass_every_check():
    res = _checks(GOOD_DOCS)
    assert {k: _status(v) for k, v in res.items()} == {
        "CHECK_PATIENT_NAME": "satisfied", "CHECK_POLICY_NUMBER": "satisfied", "CHECK_DATES": "satisfied",
        "CHECK_BILL_ARITHMETIC": "satisfied", "CHECK_CLAIMED_VS_BILLED": "satisfied",
        "CHECK_DUPLICATE_CHARGES": "satisfied"}
    assert all(f.estimated_impact is None for f in res.values())


def test_different_patient_name_is_a_mistake():
    docs = copy.deepcopy(GOOD_DOCS); docs[2]["patient_name"] = "Pooja Patel"
    f = _checks(docs)["CHECK_PATIENT_NAME"]
    assert _status(f) == "violated" and "Pooja Patel" in f.explanation
    assert f.source.document == "discharge_summary.pdf"


def test_middle_initial_needs_confirmation_only():
    docs = copy.deepcopy(GOOD_DOCS); docs[1]["patient_name"] = "Priya S Patel"
    assert _status(_checks(docs)["CHECK_PATIENT_NAME"]) == "needs_review"


def test_wrong_policy_number():
    docs = copy.deepcopy(GOOD_DOCS); docs[0]["policy_number"] = "POL-AURA-2025-001"
    f = _checks(docs)["CHECK_POLICY_NUMBER"]
    assert _status(f) == "violated" and "POL-AURA-2025-001" in f.explanation


def test_discharge_before_admission():
    docs = copy.deepcopy(GOOD_DOCS)
    for d in docs:
        d["discharge_date"] = "2026-03-08"
    assert _status(_checks(docs)["CHECK_DATES"]) == "violated"


def test_documents_disagree_on_admission_date():
    docs = copy.deepcopy(GOOD_DOCS); docs[2]["admission_date"] = "2026-03-11"
    f = _checks(docs)["CHECK_DATES"]
    assert _status(f) == "violated" and "different admission dates" in f.explanation


def test_bill_does_not_add_up():
    docs = copy.deepcopy(GOOD_DOCS); docs[1]["stated_total"] = 49400
    f = _checks(docs, claimed=49400)["CHECK_BILL_ARITHMETIC"]
    assert _status(f) == "violated" and "over by INR 2,000.00" in f.explanation


def test_claiming_more_than_billed():
    f = _checks(GOOD_DOCS, claimed=52000)["CHECK_CLAIMED_VS_BILLED"]
    assert _status(f) == "violated" and "4,600.00" in f.explanation


def test_claiming_less_than_billed_asks_to_check():
    assert _status(_checks(GOOD_DOCS, claimed=40000)["CHECK_CLAIMED_VS_BILLED"]) == "needs_review"


def test_duplicate_charge():
    docs = copy.deepcopy(GOOD_DOCS)
    docs[1]["line_items"].append({"item": "Consultation", "amount": 6000})
    docs[1]["stated_total"] = 53400
    f = _checks(docs, claimed=53400)["CHECK_DUPLICATE_CHARGES"]
    assert _status(f) == "needs_review" and "consultation" in f.explanation


def test_checks_without_data_are_skipped():
    assert run_document_checks([], POLICY) == []
    only_one = _checks(GOOD_DOCS[:1], claimed=None)
    assert "CHECK_PATIENT_NAME" not in only_one  # needs at least two documents


def test_ocr_confusion_in_name_is_not_a_mistake():
    docs = copy.deepcopy(GOOD_DOCS)
    for d in docs:
        d["patient_name"] = "Suresh Iyer"
    docs[2]["patient_name"] = "Suresh lyer"   # scanner read I as l
    assert _status(_checks(docs)["CHECK_PATIENT_NAME"]) == "satisfied"


def test_one_letter_slip_asks_to_confirm():
    docs = copy.deepcopy(GOOD_DOCS); docs[2]["patient_name"] = "Priya Patil"
    assert _status(_checks(docs)["CHECK_PATIENT_NAME"]) == "needs_review"
