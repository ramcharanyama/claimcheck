"""Offline tests for the Nemotron claim fact extractor using a fake client."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest

from src.ai.claim_extractor import ClaimExtractorError, extract_claim_facts
from src.documents.policy_document import load_document

ROOM_QUOTE = "Executive Suite Room Charges: 4 days @ INR 7500.00 per day = INR 30000.00."
ADMIT_QUOTE = "Date of admission: 2026-02-15"


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _pdf(lines):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(40, 72), "\n".join(lines), fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="module")
def documents():
    bill = load_document(_pdf([
        "CITY CARE HOSPITAL - FINAL BILL",
        ROOM_QUOTE,
        "Admission processing charges: INR 1200.00",
    ]), document_id="hospital_bill.pdf", ocr_fallback=False)
    summary = load_document(_pdf([
        "DISCHARGE SUMMARY",
        "Policy inception date: 2026-01-01",
        ADMIT_QUOTE,
        "Diagnosis: acute viral gastroenteritis (illness)",
    ]), document_id="discharge_summary.pdf", ocr_fallback=False)
    return [bill, summary]


GOOD_FACTS = [
    {
        "name": "room_rent_billing",
        "value": {"room_type": "executive_suite", "daily_rate": 7500, "days": 4, "total_amount": 30000},
        "source": {"document": "hospital_bill.pdf", "page": 1, "quote": ROOM_QUOTE},
    },
    {
        "name": "hospitalization_details",
        "value": {"policy_inception_date": "2026-01-01", "admission_date": "2026-02-15",
                  "cause": "illness", "diagnosis": "acute viral gastroenteritis", "days_elapsed": 999},
        "source": {"document": "discharge_summary.pdf", "page": 1, "quote": ADMIT_QUOTE},
    },
]


def _run(documents, facts=None, content=None, model="test-nano"):
    client = FakeClient(content if content is not None else json.dumps({"facts": facts}))
    return extract_claim_facts(documents, client=client, model=model), client


def test_correct_facts_are_verified(documents):
    result, client = _run(documents, GOOD_FACTS)
    assert [f.name for f in result.facts] == ["room_rent_billing", "hospitalization_details"]
    assert result.verified_count == 2
    assert result.needs_review == []
    assert result.rejected == []
    assert result.fact("room_rent_billing").value["daily_rate"] == 7500
    prompt = client.calls[0]["messages"][1]["content"]
    assert "=== DOCUMENT hospital_bill.pdf | PAGE 1 ===" in prompt
    assert "room_rent_billing" in prompt


def test_days_elapsed_is_computed_not_trusted(documents):
    result, _ = _run(documents, GOOD_FACTS)
    assert result.fact("hospitalization_details").value["days_elapsed"] == 45


def test_missing_date_gives_unknown_days_elapsed(documents):
    facts = copy.deepcopy(GOOD_FACTS)
    facts[1]["value"]["policy_inception_date"] = None
    result, _ = _run(documents, facts)
    assert result.fact("hospitalization_details").value["days_elapsed"] is None


def test_invented_quote_needs_review(documents):
    facts = copy.deepcopy(GOOD_FACTS)
    facts[0]["source"]["quote"] = "Room charges: 4 days at INR 3000 per day."
    result, _ = _run(documents, facts)
    assert "room_rent_billing" in result.needs_review
    assert result.citation_checks["room_rent_billing"].reason == "not_found"


def test_unknown_document_needs_review(documents):
    facts = copy.deepcopy(GOOD_FACTS)
    facts[0]["source"]["document"] = "made_up_invoice.pdf"
    result, _ = _run(documents, facts)
    assert result.citation_checks["room_rent_billing"].reason == "unknown_document"
    assert "room_rent_billing" in result.needs_review


def test_unknown_fact_name_is_rejected(documents):
    facts = copy.deepcopy(GOOD_FACTS) + [{
        "name": "patient_mood", "value": "happy",
        "source": {"document": "hospital_bill.pdf", "page": 1, "quote": ROOM_QUOTE},
    }]
    result, _ = _run(documents, facts)
    assert len(result.facts) == 2
    assert any("patient_mood" in msg for msg in result.rejected)


def test_duplicate_fact_is_rejected(documents):
    facts = copy.deepcopy(GOOD_FACTS) + [copy.deepcopy(GOOD_FACTS[0])]
    result, _ = _run(documents, facts)
    assert len(result.facts) == 2
    assert any("duplicate" in msg for msg in result.rejected)


def test_single_document_fills_missing_document_name(documents):
    facts = [copy.deepcopy(GOOD_FACTS[0])]
    del facts[0]["source"]["document"]
    result, _ = _run(documents[:1], facts)
    assert result.facts[0].source.document == "hospital_bill.pdf"
    assert result.verified_count == 1


def test_no_documents_raises():
    with pytest.raises(ClaimExtractorError):
        extract_claim_facts([], client=FakeClient("{}"), model="m")


def test_missing_facts_key_raises(documents):
    with pytest.raises(ClaimExtractorError):
        _run(documents, content='{"result": []}')


def test_uses_fast_model_by_default(documents):
    fake = FakeClient(json.dumps({"facts": GOOD_FACTS}))
    with patch("src.ai.claim_extractor.get_model", return_value="nano-id") as get_model:
        result = extract_claim_facts(documents, client=fake)
    get_model.assert_called_once_with("fast")
    assert result.model == "nano-id"


def test_non_payable_items_are_passed_to_prompt(documents):
    client = FakeClient(json.dumps({"facts": GOOD_FACTS}))
    extract_claim_facts(documents, client=client, model="m",
                        non_payable_items=["admission_processing_charges", "gloves"])
    prompt = client.calls[0]["messages"][1]["content"]
    assert "non-payable: admission_processing_charges, gloves" in prompt


def test_no_non_payable_hint_without_items(documents):
    client = FakeClient(json.dumps({"facts": GOOD_FACTS}))
    extract_claim_facts(documents, client=client, model="m")
    assert "non-payable:" not in client.calls[0]["messages"][1]["content"]


FEES_QUOTE = "Professional Fees: Surgeon INR 12000, Anesthetist INR 5000, Physician INR 3000."
OT_QUOTE = "Operation Theatre Charges: INR 15000.00."


@pytest.fixture(scope="module")
def ortho_docs():
    bill = load_document(_pdf([
        "ORTHO HOSPITAL - BILL",
        "Deluxe Room Charges: 2 days @ INR 10000.00/day = Total INR 20000.00.",
        FEES_QUOTE,
        OT_QUOTE,
    ]), document_id="hospital_bill.pdf", ocr_fallback=False)
    return [bill]


def _src(quote):
    return {"document": "hospital_bill.pdf", "page": 1, "quote": quote}


def test_professional_fees_and_ot_are_extracted(ortho_docs):
    facts = [
        {"name": "professional_fees_billing",
         "value": {"surgeon_fee": 12000, "anesthetist_fee": 5000, "consultant_fee": 3000, "total_amount": None},
         "source": _src(FEES_QUOTE)},
        {"name": "operation_theatre_billing", "value": {"ot_charges": 15000}, "source": _src(OT_QUOTE)},
    ]
    result, _ = _run(ortho_docs, facts)
    assert result.rejected == []
    assert result.fact("professional_fees_billing").value["total_amount"] == 20000.0
    assert result.fact("operation_theatre_billing").value["total_amount"] == 15000.0
    assert result.verified_count == 2


def test_stated_total_is_not_overwritten(ortho_docs):
    facts = [{"name": "professional_fees_billing",
              "value": {"surgeon_fee": 12000, "anesthetist_fee": 5000, "total_amount": 20000},
              "source": _src(FEES_QUOTE)}]
    result, _ = _run(ortho_docs, facts)
    assert result.fact("professional_fees_billing").value["total_amount"] == 20000


def test_non_cataract_surgery_is_not_forced_into_surgical_expenses(ortho_docs):
    facts = [{"name": "surgical_expenses",
              "value": {"procedure": "knee_arthroplasty", "surgeon_fee": 12000, "ot_charges": 15000},
              "source": _src(FEES_QUOTE)}]
    result, _ = _run(ortho_docs, facts)
    assert result.fact("surgical_expenses") is None
    assert any("only for cataract" in m for m in result.rejected)


def test_free_text_cause_is_not_treated_as_a_category(documents):
    facts = copy.deepcopy(GOOD_FACTS)
    facts[1]["value"]["cause"] = "elective orthopedic knee arthroplasty"
    result, _ = _run(documents, facts)
    value = result.fact("hospitalization_details").value
    assert value["cause"] is None
    assert value["reported_cause"] == "elective orthopedic knee arthroplasty"


def test_prompt_lists_new_fact_types(documents):
    _, client = _run(documents, GOOD_FACTS)
    prompt = client.calls[0]["messages"][1]["content"]
    for name in ("professional_fees_billing", "operation_theatre_billing", "patient_medical_history"):
        assert name in prompt


def test_new_v3_facts_are_accepted_and_document_quotes_checked(ortho_docs):
    facts = [
        {"name": "document_details", "value": [
            {"document": "hospital_bill.pdf", "document_type": "hospital_bill", "page": 1,
             "quote": "ORTHO HOSPITAL - BILL", "patient_name": "Deepak", "stated_total": 55000, "line_items": []},
            {"document": "hospital_bill.pdf", "document_type": "hospital_bill", "page": 1,
             "quote": "A line that is not in the bill", "patient_name": "Deepak"}],
         "source": _src("ORTHO HOSPITAL - BILL")},
        {"name": "ambulance_charges", "value": {"amount": 2500}, "source": _src(OT_QUOTE)},
        {"name": "medical_conditions_found", "value": [
            {"condition": "Type 2 diabetes", "evidence": "Tab Metformin 500 mg", "inferred_from_medicine": True}],
         "source": _src(FEES_QUOTE)},
        {"name": "declared_conditions", "value": {"conditions": []}, "source": _src(FEES_QUOTE)},
    ]
    result, client = _run(ortho_docs, facts)
    assert result.rejected == []
    entries = result.fact("document_details").value
    assert [e["quote_verified"] for e in entries] == [True, False]
    prompt = client.calls[0]["messages"][1]["content"]
    for name in ("document_details", "preauthorization_request", "medical_conditions_found", "declared_conditions"):
        assert name in prompt
