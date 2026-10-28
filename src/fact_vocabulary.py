"""Fact Vocabulary Registry for ClaimCheck 2.0.

Documents and formalizes every claim fact name accessed by the deterministic rule engine.
Provides semantic definitions, expected data types, units, and representative examples.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class FactDefinition(BaseModel):
    """Specification of an admissible claim fact in ClaimCheck 2.0."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique fact identifier matching ClaimFact.name.")
    data_type: str = Field(..., description="Expected Python/JSON data structure (e.g. list[str], dict).")
    unit: Optional[str] = Field(None, description="Physical or monetary unit, where applicable (e.g. INR, days).")
    meaning: str = Field(..., description="Semantic meaning of this fact in claims adjudication.")
    example_value: Any = Field(..., description="Representative example value illustrating structure.")


FACT_VOCABULARY: Dict[str, FactDefinition] = {
    "document_details": FactDefinition(
        name="document_details",
        data_type="list[dict]",
        unit="INR, dates",
        meaning="One entry per uploaded document (type, patient name, policy number, dates, stated total, line items, quote) used by the paperwork checks.",
        example_value=[{"document": "hospital_bill.pdf", "document_type": "hospital_bill", "page": 1,
                        "quote": "Patient: Priya Patel", "patient_name": "Priya Patel",
                        "policy_number": "POL-AURA-2026-001", "admission_date": "2026-03-10",
                        "discharge_date": "2026-03-14", "stated_total": 47400.0,
                        "line_items": [{"item": "Room charges", "amount": 30000.0}]}],
    ),
    "pre_hospitalization_expenses": FactDefinition(
        name="pre_hospitalization_expenses", data_type="list[dict]", unit="INR, dates",
        meaning="Bills for the same illness dated before admission, checked against the pre-hospitalisation window.",
        example_value=[{"description": "MRI scan", "amount": 6500.0, "date": "2026-02-20"}],
    ),
    "post_hospitalization_expenses": FactDefinition(
        name="post_hospitalization_expenses", data_type="list[dict]", unit="INR, dates",
        meaning="Bills for the same illness dated after discharge, checked against the post-hospitalisation window.",
        example_value=[{"description": "Physiotherapy", "amount": 4000.0, "date": "2026-04-10"}],
    ),
    "ambulance_charges": FactDefinition(
        name="ambulance_charges", data_type="dict", unit="INR",
        meaning="Road ambulance charge for transfer to hospital, checked against the ambulance limit.",
        example_value={"amount": 2500.0},
    ),
    "claim_amount_summary": FactDefinition(
        name="claim_amount_summary", data_type="dict", unit="INR",
        meaning="Amount claimed on the claim form (or estimate total for cashless) and amounts already paid this policy year.",
        example_value={"total_claimed": 65000.0, "previously_paid_this_year": 0.0},
    ),
    "preauthorization_request": FactDefinition(
        name="preauthorization_request", data_type="dict", unit="dates and hours",
        meaning="Cashless request details: planned or emergency, documents included, request and admission times.",
        example_value={"hospitalization_type": "planned", "documents_present": ["preauthorization_form", "cost_estimate"],
                       "request_datetime": "2026-03-05T10:00", "admission_datetime": "2026-03-10T09:00"},
    ),
    "declared_conditions": FactDefinition(
        name="declared_conditions", data_type="dict", unit=None,
        meaning="Existing medical conditions the patient declared on the claim or pre-authorisation form.",
        example_value={"conditions": ["hypertension"]},
    ),
    "medical_conditions_found": FactDefinition(
        name="medical_conditions_found", data_type="list[dict]", unit=None,
        meaning="Existing or long-term conditions found anywhere in the medical records, with evidence, for the disclosure check.",
        example_value=[{"condition": "Type 2 diabetes", "evidence": "Tab Metformin 500 mg BD",
                        "inferred_from_medicine": True, "document": "prescription.pdf"}],
    ),
    "submitted_documents": FactDefinition(
        name="submitted_documents",
        data_type="list[str]",
        unit=None,
        meaning="Identifiers of all documents submitted by the claimant (e.g. claim_form, hospital_bill, discharge_summary).",
        example_value=["claim_form", "hospital_bill", "discharge_summary"],
    ),
    "hospitalization_details": FactDefinition(
        name="hospitalization_details",
        data_type="dict",
        unit="dates and elapsed days",
        meaning="Hospital admission timeline and clinical cause details, including policy inception date, admission date, elapsed days, and cause.",
        example_value={
            "policy_inception_date": "2026-01-01",
            "admission_date": "2026-02-15",
            "days_elapsed": 45,
            "cause": "illness",
            "diagnosis": "acute viral gastroenteritis",
        },
    ),
    "room_rent_billing": FactDefinition(
        name="room_rent_billing",
        data_type="dict",
        unit="INR, days",
        meaning="Room and boarding charges billed by hospital, specifying room category, daily billing rate, duration in days, and total amount.",
        example_value={
            "room_type": "deluxe_room",
            "daily_rate": 10000.0,
            "days": 2,
            "total_amount": 20000.0,
        },
    ),
    "professional_fees_billing": FactDefinition(
        name="professional_fees_billing",
        data_type="dict",
        unit="INR",
        meaning="Itemized professional charges for treating doctors, surgeons, anesthetists, and medical consultants subject to proportional deductions.",
        example_value={
            "surgeon_fee": 12000.0,
            "anesthetist_fee": 5000.0,
            "consultant_fee": 3000.0,
            "total_amount": 20000.0,
        },
    ),
    "operation_theatre_billing": FactDefinition(
        name="operation_theatre_billing",
        data_type="dict",
        unit="INR",
        meaning="Operation theatre and surgical facility charges subject to proportional deduction when room rent exceeds daily cap.",
        example_value={
            "ot_charges": 15000.0,
            "total_amount": 15000.0,
        },
    ),
    "surgical_expenses": FactDefinition(
        name="surgical_expenses",
        data_type="dict",
        unit="INR",
        meaning="Comprehensive surgical costs for procedures governed by monetary sub-limits (e.g. cataract surgery), detailing eye treated and billed fees.",
        example_value={
            "procedure": "cataract_surgery",
            "eye": "left",
            "surgeon_fee": 30000.0,
            "ot_charges": 10000.0,
            "lens_cost": 15000.0,
            "total_cataract_billed": 55000.0,
        },
    ),
    "non_payable_items_breakdown": FactDefinition(
        name="non_payable_items_breakdown",
        data_type="list[dict]",
        unit="INR",
        meaning="Itemized list of billed administrative, registration, or disposable consumable line items evaluated against the non-payable schedule.",
        example_value=[
            {"item": "admission_processing_charges", "amount": 1200.0},
            {"item": "luxury_patient_amenity_kits", "amount": 1800.0},
        ],
    ),
    "hospital_breakdown": FactDefinition(
        name="hospital_breakdown",
        data_type="list[dict]",
        unit="INR",
        meaning="Alternative hospital invoice line item breakdown checked for disallowed administrative and consumable expenses.",
        example_value=[
            {"item": "gloves", "amount": 350.0},
            {"item": "face_masks", "amount": 150.0},
        ],
    ),
    "patient_medical_history": FactDefinition(
        name="patient_medical_history",
        data_type="dict",
        unit=None,
        meaning="Patient medical history and prior clinical diagnoses evaluated for pre-existing condition exclusions and policy ambiguities.",
        example_value={
            "diagnosis": "essential_hypertension",
            "pre_existing_history": "Diagnosed 24 months prior to policy inception",
            "hospitalization_cause": "acute hypertensive crisis",
            "claimed_amount": 18500.0,
        },
    ),
    "claim_form_summary": FactDefinition(
        name="claim_form_summary",
        data_type="dict",
        unit="INR",
        meaning="Summary financial totals declared on the signed claim form, cross-checked for conflicting amounts against hospital bills.",
        example_value={
            "claimed_amount": 35000.0,
            "total_claimed": 35000.0,
        },
    ),
    "hospital_bill_summary": FactDefinition(
        name="hospital_bill_summary",
        data_type="dict",
        unit="INR",
        meaning="Summary financial totals certified on the hospital bill, cross-referenced against claim form declarations to detect contradictory facts.",
        example_value={
            "billed_amount": 55000.0,
            "total_billed": 55000.0,
        },
    ),
}


def get_fact_definition(fact_name: str) -> Optional[FactDefinition]:
    """Retrieve the documented definition for a given fact name."""
    return FACT_VOCABULARY.get(fact_name)


def list_registered_fact_names() -> list[str]:
    """Return all registered fact names in alphabetical order."""
    return sorted(FACT_VOCABULARY.keys())
