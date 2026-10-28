"""Pipeline v3: claim types, not-applicable rules, payout summary and paperwork checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine import evaluate
from src.pipeline import EXCLUDED_RULE_TYPES, analyze, compute_payout
from src.schemas import ClaimFact, Rulebook, SourceRef

ROOT = Path(__file__).resolve().parent.parent
SRC = SourceRef(document="hospital_bill.pdf", page=1, quote="x")


@pytest.fixture(scope="module")
def gold() -> Rulebook:
    return Rulebook.model_validate(json.loads((ROOT / "data" / "gold_rulebook.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def policy_bytes() -> bytes:
    return (ROOT / "data" / "synthetic_policy.pdf").read_bytes()


def _facts(**kw):
    return [ClaimFact(name=k, value=v, source=SRC) for k, v in kw.items()]


def test_payout_subtracts_deductions(gold):
    facts = _facts(claim_amount_summary={"total_claimed": 65000},
                   ambulance_charges={"amount": 3500},
                   non_payable_items_breakdown=[{"item": "gloves", "amount": 400}])
    payout = compute_payout(gold, facts, evaluate(gold, facts), "reimbursement")
    assert payout["claimed"] == 65000
    assert payout["total_deducted"] == 1900.0  # 1500 ambulance excess + 400 gloves
    assert payout["payable"] == 63100.0
    assert payout["is_estimate"] is False


def test_payout_capped_by_remaining_sum_insured_without_double_counting(gold):
    facts = _facts(claim_amount_summary={"total_claimed": 180000, "previously_paid_this_year": 350000},
                   ambulance_charges={"amount": 3000})
    payout = compute_payout(gold, facts, evaluate(gold, facts), "reimbursement")
    assert payout["total_deducted"] == 1000.0           # sum-insured excess is NOT added here
    assert payout["payable"] == 150000.0                # remaining sum insured
    assert payout["capped_by_sum_insured"] == 29000.0   # 179000 - 150000


def test_payout_without_claimed_amount(gold):
    facts = _facts(ambulance_charges={"amount": 3000})
    payout = compute_payout(gold, facts, evaluate(gold, facts), "reimbursement")
    assert payout["claimed"] is None and payout["payable"] is None and payout["total_deducted"] == 1000.0


def test_cashless_and_reimbursement_rule_sets_differ():
    assert "cashless_preauthorization" in EXCLUDED_RULE_TYPES["reimbursement"]
    assert {"required_documents", "post_hospitalization"} <= EXCLUDED_RULE_TYPES["cashless"]


def _claim_pdfs(packet):
    folder = ROOT / "data" / "packets" / packet
    return {p.name: p.read_bytes() for p in sorted(folder.glob("*.pdf"))}


def test_mock_mode_marks_rules_without_facts_as_not_applicable(policy_bytes):
    result = analyze(policy_bytes, _claim_pdfs("packet_08"), mode="mock", packet_id="packet_08")
    assert result.errors == []
    assert "RULE_AMBULANCE_LIMIT" in result.not_applicable_rule_ids
    assert "RULE_NON_PAYABLE_ITEMS" not in result.not_applicable_rule_ids
    assert "RULE_CASHLESS_PREAUTH" not in {f.rule_id for f in result.findings}  # reimbursement claim
    assert not any("Rule [RULE_AMBULANCE_LIMIT]" in u for u in result.unresolved_items)


def test_mock_mode_only_lists_ambiguity_when_relevant(policy_bytes):
    routine = analyze(policy_bytes, _claim_pdfs("packet_08"), mode="mock", packet_id="packet_08")
    assert not any("AMB_PRE_EXISTING_CONDITIONS" in u for u in routine.unresolved_items)
    pre_existing = analyze(policy_bytes, _claim_pdfs("packet_12"), mode="mock", packet_id="packet_12")
    assert any("AMB_PRE_EXISTING_CONDITIONS" in u for u in pre_existing.unresolved_items)


def test_policy_number_read_from_policy_schedule(policy_bytes):
    result = analyze(policy_bytes, _claim_pdfs("packet_05"), mode="mock", packet_id="packet_05")
    assert result.policy_number == "POL-AURA-2026-001"


def test_cashless_claim_type_drops_reimbursement_only_rules(policy_bytes):
    result = analyze(policy_bytes, _claim_pdfs("packet_05"), mode="mock", packet_id="packet_05", claim_type="cashless")
    ids = {f.rule_id for f in result.findings}
    assert result.claim_type == "cashless"
    assert "RULE_REQ_DOCS" not in ids and "RULE_POST_HOSPITALIZATION" not in ids
    assert "RULE_CASHLESS_PREAUTH" in ids
    assert result.payout["is_estimate"] is True


def test_waiting_period_violation_makes_claim_not_payable(gold):
    facts = _facts(claim_amount_summary={"total_claimed": 30000},
                   hospitalization_details={"policy_inception_date": "2026-01-01", "admission_date": "2026-01-15",
                                            "days_elapsed": 14, "cause": "illness"})
    payout = compute_payout(gold, facts, evaluate(gold, facts), "reimbursement")
    assert payout["verdict"] == "rejected" and payout["payable"] == 0.0


def test_payout_never_starts_above_the_bills(gold):
    facts = _facts(claim_amount_summary={"total_claimed": 45000},
                   document_details=[{"document": "hospital_bill.pdf", "document_type": "hospital_bill",
                                      "stated_total": 43300}])
    payout = compute_payout(gold, facts, evaluate(gold, facts), "reimbursement")
    assert payout["payable"] == 43300.0 and payout["reduced_to_billed"] == 1700.0


def test_document_mistake_gives_fix_documents_verdict(gold):
    from src.engine.document_checks import run_document_checks
    facts = _facts(claim_amount_summary={"total_claimed": 45000},
                   document_details=[{"document": "hospital_bill.pdf", "document_type": "hospital_bill",
                                      "stated_total": 43300}])
    checks = run_document_checks(facts)
    payout = compute_payout(gold, facts, evaluate(gold, facts), "reimbursement", checks)
    assert payout["verdict"] == "fix_documents"
