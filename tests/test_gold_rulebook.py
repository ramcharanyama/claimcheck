"""Automated verification suite for synthetic policy, gold rulebook, and scenarios.

Ensures compliance with shared Pydantic schemas, verbatim quotation extraction,
1-based page indexing conventions, and deterministic claim scenario validation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
import pytest

from src.documents.pdf_extractor import extract_text_from_pdf, DocumentExtraction
from src.schemas import (
    Rulebook,
    Rule,
    RuleType,
    FindingStatus,
    ClaimScenario,
    ClaimFact,
    SourceRef,
)
from src.engine import evaluate

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
POLICY_PDF_PATH = DATA_DIR / "synthetic_policy.pdf"
RULEBOOK_JSON_PATH = DATA_DIR / "gold_rulebook.json"
TEST_CASES_DIR = DATA_DIR / "test_cases"


def normalize_whitespace(text: str) -> str:
    """Normalize multiline and consecutive whitespace into single spaces for robust quote checking."""
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def extracted_policy() -> DocumentExtraction:
    """Parse the synthetic policy PDF using the existing PDF extraction utility."""
    assert POLICY_PDF_PATH.exists(), f"Synthetic policy PDF missing at {POLICY_PDF_PATH}"
    with open(POLICY_PDF_PATH, "rb") as f:
        pdf_bytes = f.read()
    return extract_text_from_pdf(pdf_bytes, document_id="test_synthetic_policy")


@pytest.fixture(scope="module")
def loaded_rulebook() -> Rulebook:
    """Load and validate the gold rulebook against Pydantic schema."""
    assert RULEBOOK_JSON_PATH.exists(), f"Gold rulebook JSON missing at {RULEBOOK_JSON_PATH}"
    with open(RULEBOOK_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Rulebook.model_validate(data)


@pytest.fixture(scope="module")
def loaded_scenarios() -> list[ClaimScenario]:
    """Load and validate all test scenario JSON files."""
    scenario_files = sorted(TEST_CASES_DIR.glob("scenario_*.json"))
    assert len(scenario_files) >= 8, f"Expected at least 8 scenarios, found {len(scenario_files)}"

    scenarios = []
    for sc_file in scenario_files:
        with open(sc_file, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        scenario = ClaimScenario.model_validate(data)
        scenarios.append(scenario)
    return scenarios


# -----------------------------------------------------------------------------
# Test 1: Synthetic PDF exists and can be parsed by existing extractor
# -----------------------------------------------------------------------------
def test_synthetic_pdf_exists_and_parseable(extracted_policy: DocumentExtraction):
    assert extracted_policy.total_pages == 5, f"Expected 5 pages, got {extracted_policy.total_pages}"
    assert len(extracted_policy.pages) == extracted_policy.total_pages
    for page in extracted_policy.pages:
        assert len(page.text.strip()) > 50, f"Page {page.page_number} has unexpectedly low text"
        assert page.needs_ocr is False, f"Page {page.page_number} flagged for OCR unexpectedly"
        assert page.source_type == "pdf_text"


# -----------------------------------------------------------------------------
# Test 2: Gold rulebook JSON loads and validates against Pydantic schema
# -----------------------------------------------------------------------------
def test_gold_rulebook_schema_validation(loaded_rulebook: Rulebook):
    assert loaded_rulebook.policy_id == "POL-AURA-2026-001"
    assert len(loaded_rulebook.rules) == 11
    assert len(loaded_rulebook.ambiguities) == 1
    amb = loaded_rulebook.ambiguities[0]
    assert amb.ambiguity_id == "AMB_PRE_EXISTING_CONDITIONS"
    assert amb.section == "Section 4.1"
    assert amb.source.page == 4


# -----------------------------------------------------------------------------
# Test 3: All five required rule types are represented
# -----------------------------------------------------------------------------
def test_all_five_rule_types_represented(loaded_rulebook: Rulebook):
    rule_types = {r.rule_type for r in loaded_rulebook.rules}
    expected_types = {t.value for t in RuleType}  # every rule type has a gold rule
    assert rule_types == expected_types, f"Rule types mismatch. Found: {rule_types}, Expected: {expected_types}"


# -----------------------------------------------------------------------------
# Test 4: Every rule ID is unique
# -----------------------------------------------------------------------------
def test_rule_ids_unique(loaded_rulebook: Rulebook):
    rule_ids = [r.rule_id for r in loaded_rulebook.rules]
    assert len(rule_ids) == len(set(rule_ids)), f"Duplicate rule IDs detected: {rule_ids}"


# -----------------------------------------------------------------------------
# Test 5 & 6: Quotations appear verbatim on referenced 1-based PDF pages
# -----------------------------------------------------------------------------
def test_quotations_appear_verbatim_on_referenced_pages(
    extracted_policy: DocumentExtraction, loaded_rulebook: Rulebook
):
    page_text_map = {p.page_number: p.text for p in extracted_policy.pages}

    # Verify 1-based page indexing
    assert min(page_text_map.keys()) == 1
    assert max(page_text_map.keys()) == extracted_policy.total_pages

    # Check executable rules
    for rule in loaded_rulebook.rules:
        target_page = rule.source.page
        assert target_page in page_text_map, (
            f"Rule {rule.rule_id} references page {target_page}, "
            f"which does not exist in extracted PDF (pages 1..{extracted_policy.total_pages})"
        )

        page_text = page_text_map[target_page]
        norm_page = normalize_whitespace(page_text)
        norm_quote = normalize_whitespace(rule.source.quote)

        assert norm_quote in norm_page, (
            f"Quotation for rule {rule.rule_id} does not appear verbatim on Page {target_page}.\n"
            f"Quote: {norm_quote}\n"
            f"Extracted Page Text: {norm_page}"
        )

    # Check policy ambiguities (e.g. Section 4.1 on Page 4)
    for amb in loaded_rulebook.ambiguities:
        target_page = amb.source.page
        assert target_page in page_text_map, (
            f"Ambiguity {amb.ambiguity_id} references page {target_page}, "
            f"which does not exist in extracted PDF"
        )
        page_text = page_text_map[target_page]
        norm_page = normalize_whitespace(page_text)
        norm_quote = normalize_whitespace(amb.source.quote)
        assert norm_quote in norm_page, (
            f"Quotation for ambiguity {amb.ambiguity_id} does not appear verbatim on Page {target_page}."
        )


# -----------------------------------------------------------------------------
# Test 7: Every test scenario loads and has the required structure
# -----------------------------------------------------------------------------
def test_claim_scenarios_load_and_validate(loaded_scenarios: list[ClaimScenario]):
    assert len(loaded_scenarios) >= 8
    scenario_ids = [s.scenario_id for s in loaded_scenarios]
    assert len(scenario_ids) == len(set(scenario_ids)), f"Duplicate scenario IDs: {scenario_ids}"

    for sc in loaded_scenarios:
        assert len(sc.description) > 10
        assert len(sc.claim_facts) >= 1
        assert len(sc.applicable_rule_ids) >= 1
        assert len(sc.expected_findings) >= 1


# -----------------------------------------------------------------------------
# Test 8: Every scenario references valid rule IDs
# -----------------------------------------------------------------------------
def test_scenarios_reference_valid_rule_ids(
    loaded_scenarios: list[ClaimScenario], loaded_rulebook: Rulebook
):
    valid_rule_ids = {r.rule_id for r in loaded_rulebook.rules} | {
        a.ambiguity_id for a in loaded_rulebook.ambiguities
    }

    for sc in loaded_scenarios:
        for rid in sc.applicable_rule_ids:
            assert rid in valid_rule_ids, f"Scenario {sc.scenario_id} references invalid rule_id '{rid}'"

        for finding in sc.expected_findings:
            assert finding.rule_id in valid_rule_ids, (
                f"Scenario {sc.scenario_id} finding references invalid rule_id '{finding.rule_id}'"
            )


# -----------------------------------------------------------------------------
# Test 9: All required scenario categories are covered
# -----------------------------------------------------------------------------
def test_all_required_scenario_categories_covered(loaded_scenarios: list[ClaimScenario]):
    """Verify scenarios cover all 8 mandatory requirement scenarios."""
    findings_by_rule_and_status = {
        (f.rule_id, f.status)
        for sc in loaded_scenarios
        for f in sc.expected_findings
    }

    # 1. All required documents present
    assert ("RULE_REQ_DOCS", FindingStatus.SATISFIED.value) in findings_by_rule_and_status
    # 2. A required document missing
    assert ("RULE_REQ_DOCS", FindingStatus.VIOLATED.value) in findings_by_rule_and_status
    # 3. Initial waiting period clearly satisfied
    assert ("RULE_WAITING_PERIOD", FindingStatus.SATISFIED.value) in findings_by_rule_and_status
    # 4. Initial waiting period not yet satisfied
    assert ("RULE_WAITING_PERIOD", FindingStatus.VIOLATED.value) in findings_by_rule_and_status
    # 5. Room rent within the stated limit
    assert ("RULE_ROOM_RENT_CAP", FindingStatus.SATISFIED.value) in findings_by_rule_and_status
    # 6. Room rent above the stated limit
    assert ("RULE_ROOM_RENT_CAP", FindingStatus.VIOLATED.value) in findings_by_rule_and_status
    # 7. A sub-limit exceeded
    assert ("RULE_CATARACT_SUBLIMIT", FindingStatus.VIOLATED.value) in findings_by_rule_and_status
    # 8. A non-payable item present
    assert ("RULE_NON_PAYABLE_ITEMS", FindingStatus.VIOLATED.value) in findings_by_rule_and_status


# -----------------------------------------------------------------------------
# Test 10: Every expected finding uses a permitted status
# -----------------------------------------------------------------------------
def test_expected_findings_use_permitted_statuses(loaded_scenarios: list[ClaimScenario]):
    permitted_statuses = {s.value for s in FindingStatus}
    for sc in loaded_scenarios:
        for finding in sc.expected_findings:
            assert finding.status in permitted_statuses, (
                f"Scenario {sc.scenario_id} finding has unpermitted status '{finding.status}'"
            )


# -----------------------------------------------------------------------------
# Test 11: Financial impacts are not asserted without sufficient facts and policy support
# -----------------------------------------------------------------------------
def test_financial_impacts_justified_and_grounded(loaded_scenarios: list[ClaimScenario]):
    for sc in loaded_scenarios:
        # If missing information is required, financial impact must not be asserted
        if sc.missing_information_required:
            assert sc.expected_financial_impact is None, (
                f"Scenario {sc.scenario_id} asserts financial impact {sc.expected_financial_impact} "
                "despite missing_information_required being True."
            )

        # For satisfied rules, financial impact must be 0.0
        all_satisfied = all(f.status == FindingStatus.SATISFIED.value for f in sc.expected_findings)
        if all_satisfied and sc.expected_financial_impact is not None:
            assert sc.expected_financial_impact == 0.0, (
                f"Scenario {sc.scenario_id} has all satisfied findings but non-zero impact."
            )

        # When financial impact is asserted, it must be non-negative
        if sc.expected_financial_impact is not None:
            assert sc.expected_financial_impact >= 0.0


# -----------------------------------------------------------------------------
# Test 12: Room-rent rule parameters include cap, formula basis, affected categories
# -----------------------------------------------------------------------------
def test_room_rent_rule_parameters(loaded_rulebook: Rulebook):
    room_rules = [r for r in loaded_rulebook.rules if r.rule_type == RuleType.ROOM_RENT_CAP.value]
    assert len(room_rules) == 1
    rule = room_rules[0]
    assert rule.parameters.get("cap") == 5000.0
    assert rule.parameters.get("formula_basis") == "5000 / actual_daily_rate"
    affected = rule.parameters.get("affected_categories", [])
    assert "professional_fees" in affected
    assert "operation_theatre_charges" in affected


# -----------------------------------------------------------------------------
# Test 13: Scenario 11 verifies proportional room-rent deduction step by step
# -----------------------------------------------------------------------------
def test_scenario_11_proportional_deduction(loaded_scenarios: list[ClaimScenario]):
    sc11 = next((s for s in loaded_scenarios if s.scenario_id == "SCENARIO_11_ROOM_RENT_PROPORTIONAL_DEDUCTION"), None)
    assert sc11 is not None, "Scenario 11 not found in test cases"
    assert sc11.expected_financial_impact == 27500.0
    finding = sc11.expected_findings[0]
    assert finding.status == FindingStatus.VIOLATED.value
    assert finding.estimated_impact == 27500.0
    assert len(finding.calculation_steps) >= 4
    # Check that ratio and calculation steps are explicitly detailed
    steps_joined = " ".join(finding.calculation_steps)
    assert "5000 / 10000 = 0.50" in steps_joined
    assert "27500.00" in steps_joined


# -----------------------------------------------------------------------------
# Test 14: Scenario 12 verifies Section 4.1 ambiguity requires review
# -----------------------------------------------------------------------------
def test_scenario_12_ambiguity_needs_review(loaded_scenarios: list[ClaimScenario]):
    sc12 = next((s for s in loaded_scenarios if s.scenario_id == "SCENARIO_12_PRE_EXISTING_CONDITION_AMBIGUITY"), None)
    assert sc12 is not None, "Scenario 12 not found in test cases"
    assert sc12.human_review_required is True
    assert sc12.missing_information_required is True
    assert sc12.expected_financial_impact is None
    finding = sc12.expected_findings[0]
    assert finding.status == FindingStatus.NEEDS_REVIEW.value
    assert finding.rule_id == "AMB_PRE_EXISTING_CONDITIONS"


# -----------------------------------------------------------------------------
# Test 15: Detailed verification of each rule's parameter schema and types
# -----------------------------------------------------------------------------
def test_all_rules_parameter_types_and_values(loaded_rulebook: Rulebook):
    rules_map = {r.rule_id: r for r in loaded_rulebook.rules}

    # 1. RULE_REQ_DOCS
    r_docs = rules_map["RULE_REQ_DOCS"]
    assert r_docs.rule_type == RuleType.REQUIRED_DOCUMENTS.value
    assert r_docs.validation_status == "validated"
    assert r_docs.human_review_required is False
    assert set(r_docs.parameters.get("required_documents", [])) == {"claim_form", "hospital_bill", "discharge_summary"}
    assert r_docs.parameters.get("minimum_document_count") == 3
    assert r_docs.source.page == 1

    # 2. RULE_WAITING_PERIOD
    r_wait = rules_map["RULE_WAITING_PERIOD"]
    assert r_wait.rule_type == RuleType.WAITING_PERIOD.value
    assert r_wait.validation_status == "validated"
    assert r_wait.human_review_required is False
    assert r_wait.parameters.get("duration_days") == 30
    assert r_wait.parameters.get("applies_to") == "illness"
    assert r_wait.parameters.get("reference_date") == "policy_inception_date"
    assert len(r_wait.exceptions) == 1
    assert "accidental injury" in r_wait.exceptions[0].lower()
    assert r_wait.source.page == 2

    # 3. RULE_ROOM_RENT_CAP
    r_room = rules_map["RULE_ROOM_RENT_CAP"]
    assert r_room.rule_type == RuleType.ROOM_RENT_CAP.value
    assert r_room.validation_status == "validated"
    assert r_room.human_review_required is False
    assert r_room.parameters.get("cap") == 5000.0
    assert r_room.parameters.get("daily_limit_inr") == 5000.0
    assert r_room.parameters.get("formula_basis") == "5000 / actual_daily_rate"
    assert set(r_room.parameters.get("affected_categories", [])) == {"professional_fees", "operation_theatre_charges"}
    assert r_room.parameters.get("currency") == "INR"
    assert r_room.parameters.get("unit") == "per_day"
    assert r_room.parameters.get("excess_payable_by") == "insured"
    assert r_room.source.page == 2

    # 4. RULE_CATARACT_SUBLIMIT
    r_cat = rules_map["RULE_CATARACT_SUBLIMIT"]
    assert r_cat.rule_type == RuleType.SUB_LIMIT.value
    assert r_cat.validation_status == "validated"
    assert r_cat.human_review_required is False
    assert r_cat.parameters.get("category") == "cataract_surgery"
    assert r_cat.parameters.get("max_limit_inr") == 40000.0
    assert r_cat.parameters.get("currency") == "INR"
    assert r_cat.parameters.get("unit") == "per_eye"
    assert set(r_cat.parameters.get("inclusive_items", [])) == {
        "surgeon_fees", "operating_room_charges", "intraocular_lens_costs"
    }
    assert r_cat.source.page == 3

    # 5. RULE_NON_PAYABLE_ITEMS
    r_non = rules_map["RULE_NON_PAYABLE_ITEMS"]
    assert r_non.rule_type == RuleType.NON_PAYABLE_ITEMS.value
    assert r_non.validation_status == "validated"
    assert r_non.human_review_required is False
    expected_non_payable = {
        "registration_fees",
        "admission_processing_charges",
        "gloves",
        "face_masks",
        "sanitizers",
        "thermometer_charges",
        "luxury_patient_amenity_kits",
    }
    assert set(r_non.parameters.get("excluded_items", [])) == expected_non_payable
    assert len(r_non.parameters.get("excluded_items", [])) == 7
    assert r_non.source.page == 3

    # Ambiguity: AMB_PRE_EXISTING_CONDITIONS
    amb = loaded_rulebook.ambiguities[0]
    assert amb.ambiguity_id == "AMB_PRE_EXISTING_CONDITIONS"
    assert amb.section == "Section 4.1"
    assert "undefined regulatory reference" in amb.issue.lower()
    assert amb.source.page == 4


# -----------------------------------------------------------------------------
# Test 16: Quotation mismatch detection rejects altered or hallucinated text
# -----------------------------------------------------------------------------
def test_quotation_mismatch_fails_verification(extracted_policy: DocumentExtraction):
    page_text_map = {p.page_number: p.text for p in extracted_policy.pages}

    # Altered quotation that changes policy terms (e.g. INR 10000 instead of 5000)
    hallucinated_quote = "The maximum eligible room and boarding expense covered by the insurer is INR 10000 per day."
    norm_page_2 = normalize_whitespace(page_text_map[2])
    norm_hallucinated = normalize_whitespace(hallucinated_quote)
    assert norm_hallucinated not in norm_page_2, "Hallucinated quote should not match page 2 text"


# -----------------------------------------------------------------------------
# Test 17: Out of bounds citation page numbers are rejected
# -----------------------------------------------------------------------------
def test_citation_page_out_of_bounds_detected(extracted_policy: DocumentExtraction):
    total_pages = extracted_policy.total_pages
    assert total_pages == 5

    invalid_pages = [0, total_pages + 1, -1, 99]
    valid_pages = {p.page_number for p in extracted_policy.pages}
    for p in invalid_pages:
        assert p not in valid_pages, f"Page {p} should be detected as out-of-bounds"


# -----------------------------------------------------------------------------
# Test 18: Quotation cited on wrong page is detected as mismatch
# -----------------------------------------------------------------------------
def test_quotation_on_wrong_page_detected(extracted_policy: DocumentExtraction, loaded_rulebook: Rulebook):
    page_text_map = {p.page_number: p.text for p in extracted_policy.pages}
    # Section 1.1 quote belongs on Page 1, not Page 2
    rule_docs = next(r for r in loaded_rulebook.rules if r.rule_id == "RULE_REQ_DOCS")
    norm_quote = normalize_whitespace(rule_docs.source.quote)
    norm_page_2 = normalize_whitespace(page_text_map[2])
    assert norm_quote not in norm_page_2, "Page 1 quote should not be found on Page 2"


# -----------------------------------------------------------------------------
# Test 19: Schema rejects invalid rule type
# -----------------------------------------------------------------------------
def test_schema_rejects_unsupported_rule_type():
    with pytest.raises(Exception):
        Rule(
            rule_id="RULE_INVALID_TYPE",
            rule_type="co_payment_clause",
            description="Unsupported rule type",
            parameters={},
            source=SourceRef(document="policy.pdf", page=1, quote="Some text"),
        )


# -----------------------------------------------------------------------------
# Test 20: Room rent rate boundaries: below cap, at cap, above cap
# -----------------------------------------------------------------------------
def test_room_rent_rate_boundaries(loaded_rulebook: Rulebook):
    # Below cap: INR 4000/day
    f_below = [ClaimFact(
        name="room_rent_billing",
        value={"daily_rate": 4000.0, "days": 3, "total_amount": 12000.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Daily rate 4000 for 3 days"),
    )]
    res_below = evaluate(loaded_rulebook, f_below, ["RULE_ROOM_RENT_CAP"])
    assert res_below[0].status == FindingStatus.SATISFIED.value
    assert res_below[0].estimated_impact == 0.0

    # Exactly at cap: INR 5000/day
    f_at = [ClaimFact(
        name="room_rent_billing",
        value={"daily_rate": 5000.0, "days": 3, "total_amount": 15000.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Daily rate 5000 for 3 days"),
    )]
    res_at = evaluate(loaded_rulebook, f_at, ["RULE_ROOM_RENT_CAP"])
    assert res_at[0].status == FindingStatus.SATISFIED.value
    assert res_at[0].estimated_impact == 0.0

    # Above cap: INR 5001/day
    f_above = [ClaimFact(
        name="room_rent_billing",
        value={"daily_rate": 5001.0, "days": 2, "total_amount": 10002.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Daily rate 5001 for 2 days"),
    )]
    res_above = evaluate(loaded_rulebook, f_above, ["RULE_ROOM_RENT_CAP"])
    assert res_above[0].status == FindingStatus.VIOLATED.value
    assert res_above[0].estimated_impact == 2.0


# -----------------------------------------------------------------------------
# Test 21: Room rent above cap with unknown duration keeps impact unknown
# -----------------------------------------------------------------------------
def test_room_rent_above_cap_missing_duration_keeps_impact_unknown(loaded_rulebook: Rulebook):
    f_unknown = [ClaimFact(
        name="room_rent_billing",
        value={"daily_rate": 7500.0, "days": None},
        source=SourceRef(document="bill.pdf", page=1, quote="Daily rate 7500, stay duration unspecified"),
    )]
    res = evaluate(loaded_rulebook, f_unknown, ["RULE_ROOM_RENT_CAP"])
    assert res[0].status == FindingStatus.NEEDS_REVIEW.value
    assert res[0].estimated_impact is None
    assert "duration" in res[0].explanation.lower() or "days" in res[0].explanation.lower()


# -----------------------------------------------------------------------------
# Test 22: Cataract surgery sub-limit: below, equal, above, and bilateral
# -----------------------------------------------------------------------------
def test_cataract_sublimit_boundaries_and_bilateral(loaded_rulebook: Rulebook):
    # Exactly at sublimit (single eye: INR 40000)
    f_at = [ClaimFact(
        name="surgical_expenses",
        value={"procedure": "cataract_surgery", "eyes_treated": 1, "total_cataract_billed": 40000.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Cataract surgery INR 40000"),
    )]
    res_at = evaluate(loaded_rulebook, f_at, ["RULE_CATARACT_SUBLIMIT"])
    assert res_at[0].status == FindingStatus.SATISFIED.value
    assert res_at[0].estimated_impact == 0.0

    # Above sublimit (single eye: INR 45000)
    f_above = [ClaimFact(
        name="surgical_expenses",
        value={"procedure": "cataract_surgery", "eyes_treated": 1, "total_cataract_billed": 45000.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Cataract surgery INR 45000"),
    )]
    res_above = evaluate(loaded_rulebook, f_above, ["RULE_CATARACT_SUBLIMIT"])
    assert res_above[0].status == FindingStatus.VIOLATED.value
    assert res_above[0].estimated_impact == 5000.0

    # Bilateral (2 eyes: effective sublimit = INR 80000) within limit
    f_bi_within = [ClaimFact(
        name="surgical_expenses",
        value={"procedure": "cataract_surgery", "eyes_treated": 2, "total_cataract_billed": 78000.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Bilateral cataract surgery INR 78000"),
    )]
    res_bi_within = evaluate(loaded_rulebook, f_bi_within, ["RULE_CATARACT_SUBLIMIT"])
    assert res_bi_within[0].status == FindingStatus.SATISFIED.value
    assert res_bi_within[0].estimated_impact == 0.0

    # Bilateral (2 eyes) exceeded
    f_bi_exceeded = [ClaimFact(
        name="surgical_expenses",
        value={"procedure": "cataract_surgery", "eyes_treated": 2, "total_cataract_billed": 92000.0},
        source=SourceRef(document="bill.pdf", page=1, quote="Bilateral cataract surgery INR 92000"),
    )]
    res_bi_exceeded = evaluate(loaded_rulebook, f_bi_exceeded, ["RULE_CATARACT_SUBLIMIT"])
    assert res_bi_exceeded[0].status == FindingStatus.VIOLATED.value
    assert res_bi_exceeded[0].estimated_impact == 12000.0


# -----------------------------------------------------------------------------
# Test 23: Proportional deduction applies only to professional fees and OT
# -----------------------------------------------------------------------------
def test_proportional_deduction_exempts_other_hospital_charges(loaded_rulebook: Rulebook):
    facts = [
        ClaimFact(
            name="room_rent_billing",
            value={"daily_rate": 10000.0, "days": 2, "total_amount": 20000.0},
            source=SourceRef(document="bill.pdf", page=1, quote="Room rent 10000/day for 2 days"),
        ),
        ClaimFact(
            name="professional_fees_billing",
            value={"surgeon_fee": 10000.0, "total_amount": 10000.0},
            source=SourceRef(document="bill.pdf", page=1, quote="Surgeon fee 10000"),
        ),
        ClaimFact(
            name="medicines_billing",
            value={"pharmacy_total": 20000.0},
            source=SourceRef(document="bill.pdf", page=1, quote="Inpatient pharmacy charges 20000"),
        ),
    ]
    res = evaluate(loaded_rulebook, facts, ["RULE_ROOM_RENT_CAP"])
    assert res[0].status == FindingStatus.VIOLATED.value
    assert res[0].estimated_impact == 15000.0


# -----------------------------------------------------------------------------
# Test 24: Non-payable items schedule: all 7 excluded items recognized
# -----------------------------------------------------------------------------
def test_all_seven_non_payable_items_recognized(loaded_rulebook: Rulebook):
    rule_non = next(r for r in loaded_rulebook.rules if r.rule_id == "RULE_NON_PAYABLE_ITEMS")
    excluded = rule_non.parameters["excluded_items"]

    for item in excluded:
        facts = [ClaimFact(
            name="non_payable_items_breakdown",
            value=[{"item": item, "amount": 500.0}],
            source=SourceRef(document="bill.pdf", page=1, quote=f"Billed line item: {item} INR 500"),
        )]
        res = evaluate(loaded_rulebook, facts, ["RULE_NON_PAYABLE_ITEMS"])
        assert res[0].status == FindingStatus.VIOLATED.value, f"Excluded item '{item}' was not recognized as violated"
        assert res[0].estimated_impact == 500.0


# -----------------------------------------------------------------------------
# Test 25: Unverified or draft rules mandate human review
# -----------------------------------------------------------------------------
def test_unverified_rule_status_forces_human_review(loaded_rulebook: Rulebook):
    draft_rules = []
    for r in loaded_rulebook.rules:
        r_dict = r.model_dump()
        if r.rule_id == "RULE_REQ_DOCS":
            r_dict["validation_status"] = "draft"
        draft_rules.append(Rule.model_validate(r_dict))

    draft_rulebook = Rulebook(
        policy_id=loaded_rulebook.policy_id,
        rules=draft_rules,
        ambiguities=loaded_rulebook.ambiguities,
    )

    facts = [ClaimFact(
        name="submitted_documents",
        value=["claim_form", "hospital_bill", "discharge_summary"],
        source=SourceRef(document="claim.pdf", page=1, quote="All docs submitted"),
    )]
    res = evaluate(draft_rulebook, facts, ["RULE_REQ_DOCS"])
    assert res[0].status == FindingStatus.NEEDS_REVIEW.value
    assert res[0].estimated_impact is None
    assert "unverified" in res[0].explanation.lower() or "draft" in res[0].explanation.lower()


# -----------------------------------------------------------------------------
# Test 26: Section 4.1 ambiguity requires review without guessing regulations
# -----------------------------------------------------------------------------
def test_ambiguity_requires_review_without_guessing(loaded_rulebook: Rulebook):
    facts = [ClaimFact(
        name="patient_medical_history",
        value={"diagnosis": "type_2_diabetes", "pre_existing_history": "Diagnosed 3 years prior"},
        source=SourceRef(document="history.pdf", page=1, quote="History of diabetes"),
    )]
    res = evaluate(loaded_rulebook, facts, ["AMB_PRE_EXISTING_CONDITIONS"])
    assert len(res) == 1
    assert res[0].status == FindingStatus.NEEDS_REVIEW.value
    assert res[0].estimated_impact is None
    assert "ambiguous" in res[0].explanation.lower() or "undefined" in res[0].explanation.lower()
