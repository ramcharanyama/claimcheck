"""Robustness and edge-case test suite for the deterministic rule engine.

Tests invalid, incomplete, conflicting, unparseable, and unverified inputs,
verifying that evaluate() never raises unexpected exceptions, handles missing
parameters gracefully, returns unable_to_verify or needs_review, and never
asserts unsupported violated findings.
"""

from __future__ import annotations

import pytest
from src.schemas import (
    Rule,
    Rulebook,
    RuleType,
    ClaimFact,
    FindingStatus,
    SourceRef,
    Ambiguity,
    ValidationStatus,
)
from src.engine import evaluate


def _dummy_source(page: int = 1) -> SourceRef:
    return SourceRef(document="policy.pdf", page=page, quote="Operative clause quote")


# -----------------------------------------------------------------------------
# Test 1: Missing, null, or incorrectly named parameters
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rule_type,invalid_params",
    [
        (RuleType.REQUIRED_DOCUMENTS, {}),
        (RuleType.REQUIRED_DOCUMENTS, {"wrong_key": ["claim_form"]}),
        (RuleType.REQUIRED_DOCUMENTS, {"required_documents": []}),
        (RuleType.WAITING_PERIOD, {}),
        (RuleType.WAITING_PERIOD, {"duration_days": None}),
        (RuleType.WAITING_PERIOD, {"duration_days": "not_an_int"}),
        (RuleType.ROOM_RENT_CAP, {}),
        (RuleType.ROOM_RENT_CAP, {"cap": None}),
        (RuleType.ROOM_RENT_CAP, {"cap": "invalid_num"}),
        (RuleType.ROOM_RENT_CAP, {"daily_limit_inr": -500}),
        (RuleType.SUB_LIMIT, {}),
        (RuleType.SUB_LIMIT, {"category": "", "max_limit_inr": 40000}),
        (RuleType.SUB_LIMIT, {"category": "cataract", "max_limit_inr": "invalid"}),
        (RuleType.NON_PAYABLE_ITEMS, {}),
        (RuleType.NON_PAYABLE_ITEMS, {"excluded_items": None}),
    ],
)
def test_missing_or_invalid_parameters_return_unable_to_verify(rule_type: RuleType, invalid_params: dict):
    rule = Rule(
        rule_id="RULE_TEST_PARAM",
        rule_type=rule_type,
        description="Test parameter robustness",
        parameters=invalid_params,
        exceptions=[],
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
        human_review_required=False,
    )
    rb = Rulebook(policy_id="POL-TEST", rules=[rule])

    # Provide a realistic fact that would otherwise trigger evaluation
    facts = [
        ClaimFact(name="submitted_documents", value=["claim_form"], source=_dummy_source()),
        ClaimFact(name="hospitalization_details", value={"days_elapsed": 10, "cause": "illness"}, source=_dummy_source()),
        ClaimFact(name="room_rent_billing", value={"daily_rate": 8000.0, "days": 3}, source=_dummy_source()),
        ClaimFact(name="surgical_expenses", value={"procedure": "cataract", "total_billed": 50000.0}, source=_dummy_source()),
        ClaimFact(name="non_payable_items_breakdown", value=[{"item": "gloves", "amount": 100}], source=_dummy_source()),
    ]

    findings = evaluate(rb, facts)
    assert len(findings) == 1
    f = findings[0]
    # Parameter defect must result in unable_to_verify, never an exception or violated
    assert f.status == FindingStatus.UNABLE_TO_VERIFY.value
    assert f.estimated_impact is None
    assert f.rule_id == "RULE_TEST_PARAM"


# -----------------------------------------------------------------------------
# Test 2: Unverified rules (draft, deprecated, provisional, human review required)
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status,human_review",
    [
        (ValidationStatus.DRAFT, False),
        (ValidationStatus.DEPRECATED, False),
        (ValidationStatus.VALIDATED, True),
    ],
)
def test_unverified_rules_require_human_review(status: Any, human_review: bool):
    rule = Rule(
        rule_id="RULE_UNVERIFIED",
        rule_type=RuleType.ROOM_RENT_CAP,
        description="Room rent cap unverified test",
        parameters={"cap": 5000.0},
        exceptions=[],
        source=_dummy_source(),
        validation_status=status,
        human_review_required=human_review,
    )
    rb = Rulebook(policy_id="POL-TEST", rules=[rule])
    facts = [
        ClaimFact(name="room_rent_billing", value={"daily_rate": 8000.0, "days": 3}, source=_dummy_source())
    ]

    findings = evaluate(rb, facts)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.NEEDS_REVIEW.value
    assert findings[0].estimated_impact is None


# -----------------------------------------------------------------------------
# Test 3: Duplicate or conflicting facts
# -----------------------------------------------------------------------------
def test_conflicting_facts_trigger_needs_review():
    """Conflicting duplicate facts or conflicting total amounts across documents flag needs_review."""
    rule = Rule(
        rule_id="RULE_ROOM",
        rule_type=RuleType.ROOM_RENT_CAP,
        description="Room rent cap",
        parameters={"cap": 5000.0},
        exceptions=[],
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
        human_review_required=False,
    )
    rb = Rulebook(policy_id="POL-TEST", rules=[rule])

    # Two duplicate facts with contradictory rates
    conflicting_facts = [
        ClaimFact(name="room_rent_billing", value={"daily_rate": 4000.0, "days": 3}, source=_dummy_source()),
        ClaimFact(name="room_rent_billing", value={"daily_rate": 8000.0, "days": 3}, source=_dummy_source()),
    ]

    findings = evaluate(rb, conflicting_facts)
    assert len(findings) == 1
    f = findings[0]
    assert f.status == FindingStatus.NEEDS_REVIEW.value
    assert f.estimated_impact is None
    assert "conflicting" in f.explanation.lower() or "contradict" in f.explanation.lower()


def test_cross_document_amount_conflicts_trigger_needs_review():
    """Contradiction between claimed amount on form vs billed amount on hospital bill."""
    rule = Rule(
        rule_id="RULE_ROOM",
        rule_type=RuleType.ROOM_RENT_CAP,
        description="Room rent cap",
        parameters={"cap": 5000.0},
        exceptions=[],
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
        human_review_required=False,
    )
    rb = Rulebook(policy_id="POL-TEST", rules=[rule])

    conflicting_amounts = [
        ClaimFact(name="claim_form_summary", value={"claimed_amount": 35000.0}, source=_dummy_source()),
        ClaimFact(name="hospital_bill_summary", value={"billed_amount": 55000.0}, source=_dummy_source()),
        ClaimFact(name="room_rent_billing", value={"daily_rate": 4000.0, "days": 3}, source=_dummy_source()),
    ]

    findings = evaluate(rb, conflicting_amounts)
    assert len(findings) == 1
    assert findings[0].status == FindingStatus.NEEDS_REVIEW.value
    assert findings[0].estimated_impact is None


# -----------------------------------------------------------------------------
# Test 4: Unparseable numbers and dates
# -----------------------------------------------------------------------------
def test_unparseable_numbers_and_dates_never_crash():
    """Unparseable numbers, corrupted dates, and unexpected types yield unable_to_verify without crashing."""
    rule_room = Rule(
        rule_id="RULE_ROOM",
        rule_type=RuleType.ROOM_RENT_CAP,
        description="Room rent",
        parameters={"cap": 5000.0},
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
    )
    rule_wait = Rule(
        rule_id="RULE_WAIT",
        rule_type=RuleType.WAITING_PERIOD,
        description="Waiting period",
        parameters={"duration_days": 30},
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
    )
    rule_sub = Rule(
        rule_id="RULE_SUB",
        rule_type=RuleType.SUB_LIMIT,
        description="Sublimit",
        parameters={"category": "cataract", "max_limit_inr": 40000.0},
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
    )
    rb = Rulebook(policy_id="POL-TEST", rules=[rule_room, rule_wait, rule_sub])

    corrupted_facts = [
        ClaimFact(name="room_rent_billing", value={"daily_rate": "NaN_corrupted", "days": "unknown"}, source=_dummy_source()),
        ClaimFact(
            name="hospitalization_details",
            value={"admission_date": "not-a-date", "policy_inception_date": "corrupted", "days_elapsed": "None"},
            source=_dummy_source(),
        ),
        ClaimFact(name="surgical_expenses", value={"procedure": "cataract", "total_billed": "five_hundred"}, source=_dummy_source()),
    ]

    findings = evaluate(rb, corrupted_facts)
    assert len(findings) == 3
    for f in findings:
        assert f.status == FindingStatus.UNABLE_TO_VERIFY.value
        assert f.estimated_impact is None


# -----------------------------------------------------------------------------
# Test 5: Generic ambiguity evaluation without hardcoded rule IDs
# -----------------------------------------------------------------------------
def test_generic_ambiguity_evaluation_without_hardcoded_id():
    """Ambiguity evaluation discovers relevance based on terms and issue description without hardcoded ID."""
    amb = Ambiguity(
        ambiguity_id="CUSTOM_AMB_001",
        section="Section 5.3",
        description="Experimental treatments excluded unless authorized by designated authorities under relevant standards.",
        issue="Undefined designated authority and relevant standards",
        source=_dummy_source(page=3),
    )
    dummy_rule = Rule(
        rule_id="RULE_DUMMY",
        rule_type=RuleType.ROOM_RENT_CAP,
        description="Dummy rule",
        parameters={"cap": 5000.0},
        source=_dummy_source(),
        validation_status=ValidationStatus.VALIDATED,
    )
    rb = Rulebook(policy_id="POL-TEST", rules=[dummy_rule], ambiguities=[amb])

    # Relevant fact matching description terms
    relevant_fact = [
        ClaimFact(
            name="experimental_therapy_record",
            value={"treatment": "experimental gene therapy", "cost": 150000.0},
            source=_dummy_source(),
        )
    ]
    findings = evaluate(rb, relevant_fact)
    findings_by_id = {f.rule_id: f for f in findings}
    assert "CUSTOM_AMB_001" in findings_by_id
    amb_f = findings_by_id["CUSTOM_AMB_001"]
    assert amb_f.status == FindingStatus.NEEDS_REVIEW.value
    assert amb_f.estimated_impact is None

    # Irrelevant fact: ambiguity is unable_to_verify when explicitly requested
    irrelevant_fact = [
        ClaimFact(name="submitted_documents", value=["claim_form"], source=_dummy_source())
    ]
    findings_irr = evaluate(rb, irrelevant_fact, rule_ids=["CUSTOM_AMB_001"])
    assert len(findings_irr) == 1
    assert findings_irr[0].status == FindingStatus.UNABLE_TO_VERIFY.value


# -----------------------------------------------------------------------------
# Ambiguity relevance: subject words decide, not generic legal words
# -----------------------------------------------------------------------------
def _gold_rulebook_with_pre_existing_ambiguity():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "data" / "gold_rulebook.json"
    return Rulebook.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _amb_statuses(rulebook, facts):
    return {f.rule_id: str(getattr(f.status, "value", f.status))
            for f in evaluate(rulebook, facts) if f.rule_id.startswith("AMB")}


_SRC = SourceRef(document="claim_form.pdf", page=1, quote="x")


def test_ambiguity_not_triggered_by_generic_word_policy():
    rb = _gold_rulebook_with_pre_existing_ambiguity()
    facts = [ClaimFact(name="claim_form_summary",
                       value={"policy_number": "POL-AURA-2026-001", "note": "Claim filed under policy for dengue fever"},
                       source=_SRC)]
    assert _amb_statuses(rb, facts) == {}


def test_ambiguity_triggered_by_pre_existing_history_without_word_policy():
    rb = _gold_rulebook_with_pre_existing_ambiguity()
    facts = [ClaimFact(name="patient_medical_history",
                       value={"diagnosis": "type_2_diabetes",
                              "pre_existing_history": "Known diabetic for 5 years before enrolment",
                              "hospitalization_cause": "diabetic ketoacidosis", "claimed_amount": 42000},
                       source=_SRC)]
    assert _amb_statuses(rb, facts) == {"AMB_PRE_EXISTING_CONDITIONS": "needs_review"}


def test_empty_pre_existing_history_is_not_evidence():
    rb = _gold_rulebook_with_pre_existing_ambiguity()
    facts = [ClaimFact(name="patient_medical_history",
                       value={"diagnosis": "dengue_fever", "pre_existing_history": None,
                              "hospitalization_cause": "dengue fever", "claimed_amount": None},
                       source=_SRC)]
    assert _amb_statuses(rb, facts) == {}


def test_ambiguity_matching_works_for_differently_worded_ambiguity():
    rb = _gold_rulebook_with_pre_existing_ambiguity()
    reworded = rb.ambiguities[0].model_copy(update={
        "ambiguity_id": "AMB_pre_existing_definition",
        "issue": "Missing definitions for Pre-Existing Condition and statutory waiting period",
        "description": "The clause refers to a Pre-Existing Condition as defined under applicable regulations "
                       "and a statutory waiting period, but neither term is defined in the policy.",
    })
    rb = rb.model_copy(update={"ambiguities": [reworded]})
    history = [ClaimFact(name="patient_medical_history",
                         value={"pre_existing_history": "Hypertension for 2 years"}, source=_SRC)]
    routine = [ClaimFact(name="claim_form_summary", value={"note": "policy waiting period served"}, source=_SRC)]
    assert _amb_statuses(rb, history) == {"AMB_pre_existing_definition": "needs_review"}
    assert _amb_statuses(rb, routine) == {}
