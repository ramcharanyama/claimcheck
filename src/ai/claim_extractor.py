"""Claim Fact Extractor for ClaimCheck 2.0.

Reads claim documents (hospital bills, discharge summaries) and extracts
structured ClaimFact objects using a fast NVIDIA Nemotron model (Nano) via
Nebius Token Factory. Every fact must cite a document, a page and a verbatim
quote, and every citation is verified against the extracted document text.

Arithmetic is never left to the model: days_elapsed is recomputed from dates.
Facts whose citation cannot be verified are listed in `needs_review`.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from src.ai.claim_loader import load_claim_document
from src.ai.nebius_client import chat_json, get_client, get_model
from src.documents.evidence import CitationCheck, check_citation
from src.documents.pdf_extractor import DocumentExtraction
from src.schemas import ClaimFact

ALLOWED_FACTS = (
    "submitted_documents",
    "hospitalization_details",
    "room_rent_billing",
    "surgical_expenses",
    "non_payable_items_breakdown",
    "professional_fees_billing",
    "operation_theatre_billing",
    "patient_medical_history",
)

ALLOWED_CAUSES = ("illness", "accidental_injury")


class ClaimExtractorError(Exception):
    """Raised when there are no documents or the model response has no facts list."""


class ClaimExtractionResult(BaseModel):
    model: str
    facts: List[ClaimFact] = Field(default_factory=list)
    citation_checks: Dict[str, CitationCheck] = Field(default_factory=dict)
    needs_review: List[str] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)

    @property
    def verified_count(self) -> int:
        return sum(c.verified for c in self.citation_checks.values())

    def fact(self, name: str) -> Optional[ClaimFact]:
        return next((f for f in self.facts if f.name == name), None)


SYSTEM_PROMPT = (
    "You are a medical claims analyst. Extract structured facts from hospital claim documents. "
    "Use only what the documents state. Never guess or calculate missing values. "
    "Every fact must cite one document and page and copy its quote from that page word for word. "
    "Respond with a single JSON object and nothing else."
)

FACT_SCHEMA = """Use exactly these fact names and value shapes:
- submitted_documents: list of document types included in the claim, e.g. ["claim_form", "hospital_bill", "discharge_summary"]
- hospitalization_details: {"policy_inception_date": "YYYY-MM-DD" or null, "admission_date": "YYYY-MM-DD" or null, "cause": "illness" or "accidental_injury", "diagnosis": text}
- room_rent_billing: {"room_type": snake_case text, "daily_rate": number, "days": integer or null, "total_amount": number or null}
- surgical_expenses: ONLY for cataract surgery. {"procedure": "cataract_surgery", "eye": "left", "right" or null, "surgeon_fee": number, "ot_charges": number, "lens_cost": number, "total_cataract_billed": number}
- professional_fees_billing: doctor fees for any other treatment. {"surgeon_fee": number or null, "anesthetist_fee": number or null, "consultant_fee": number or null (physician or consultant), "total_amount": number or null}
- operation_theatre_billing: operation theatre charges for any other treatment. {"ot_charges": number, "total_amount": number or null}
- patient_medical_history: prior conditions mentioned in the documents. {"diagnosis": snake_case text, "pre_existing_history": text as stated, "hospitalization_cause": text, "claimed_amount": number or null}
- non_payable_items_breakdown: list of {"item": snake_case name, "amount": number}
Omit a fact entirely if the documents do not mention it. Use null for any value that is not stated.
Write amounts as plain numbers in INR without commas or currency symbols."""

NON_PAYABLE_HINT = """The policy lists these items as non-payable: {items}.
For non_payable_items_breakdown, include every billed line item that matches one of them (same meaning,
even if worded differently), using the policy's name for "item" and the billed amount. Quote the bill lines."""

USER_TEMPLATE = """Claim documents: {document_ids}

Return JSON in exactly this shape:
{{"facts": [{{
  "name": "<one of the fact names below>",
  "value": <value in the shape below>,
  "source": {{"document": "<document id>", "page": <page number>, "quote": "<exact text copied from that page>"}}
}}]}}

{fact_schema}
{non_payable_hint}
Document text, page by page:
{pages}"""


def _format_documents(documents: List[DocumentExtraction]) -> str:
    blocks = []
    for doc in documents:
        for page in doc.pages:
            blocks.append(f"=== DOCUMENT {doc.document_id} | PAGE {page.page_number} ===\n{page.text.strip()}")
    return "\n\n".join(blocks)


def _parse_date(value) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _normalize_value(name: str, value):
    """Deterministic post-processing: recompute derived numbers instead of trusting the model."""
    if name == "hospitalization_details" and isinstance(value, dict):
        value = dict(value)
        cause = value.get("cause")
        if isinstance(cause, str) and cause.strip().lower() not in ALLOWED_CAUSES:
            # Keep what the model read, but never let free text pose as a cause category.
            value["reported_cause"] = cause
            value["cause"] = None
        inception = _parse_date(value.get("policy_inception_date"))
        admission = _parse_date(value.get("admission_date"))
        value["days_elapsed"] = (admission - inception).days if inception and admission else None
    if name in ("professional_fees_billing", "operation_theatre_billing") and isinstance(value, dict):
        value = dict(value)
        parts = [v for k, v in value.items() if k != "total_amount" and isinstance(v, (int, float))]
        if value.get("total_amount") is None and parts:
            value["total_amount"] = float(sum(parts))
    return value


def _check(documents: Dict[str, DocumentExtraction], fact: ClaimFact) -> CitationCheck:
    doc = documents.get(fact.source.document)
    if doc is None:
        return CitationCheck(verified=False, cited_page=fact.source.page, reason="unknown_document")
    return check_citation(doc, fact.source)


def extract_claim_facts(
    documents: List[DocumentExtraction],
    client=None,
    model: Optional[str] = None,
    non_payable_items: Optional[List[str]] = None,
) -> ClaimExtractionResult:
    """Ask Nemotron (fast model) for claim facts, validate them, and verify every citation.

    non_payable_items: item names from the policy's non-payable rule (e.g. a compiled
    rule's parameters["excluded_items"]). Without it the model cannot know which bill
    lines the policy excludes, so non_payable_items_breakdown is usually omitted.
    """
    if not documents:
        raise ClaimExtractorError("No claim documents provided.")

    client = client or get_client()
    model = model or get_model("fast")
    by_id = {doc.document_id: doc for doc in documents}

    user = USER_TEMPLATE.format(
        document_ids=", ".join(by_id),
        fact_schema=FACT_SCHEMA,
        non_payable_hint=(
            "\n" + NON_PAYABLE_HINT.format(items=", ".join(non_payable_items)) + "\n"
            if non_payable_items else ""
        ),
        pages=_format_documents(documents),
    )
    data = chat_json(client, model, SYSTEM_PROMPT, user)

    raw_facts = data.get("facts")
    if not isinstance(raw_facts, list):
        raise ClaimExtractorError("Model response has no 'facts' list.")

    result = ClaimExtractionResult(model=model)
    default_doc = documents[0].document_id if len(documents) == 1 else None

    for index, raw in enumerate(raw_facts, start=1):
        if not isinstance(raw, dict):
            result.rejected.append(f"fact #{index}: not a JSON object")
            continue
        name = raw.get("name")
        if name not in ALLOWED_FACTS:
            result.rejected.append(f"fact #{index}: unknown fact name {name!r}")
            continue
        if name == "surgical_expenses":
            value = raw.get("value") if isinstance(raw.get("value"), dict) else {}
            if "cataract" not in str(value.get("procedure", "")).lower():
                result.rejected.append(
                    f"fact #{index}: surgical_expenses is only for cataract surgery "
                    f"(got procedure {value.get('procedure')!r})"
                )
                continue
        if result.fact(name) is not None:
            result.rejected.append(f"fact #{index}: duplicate fact {name}")
            continue

        src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        try:
            fact = ClaimFact.model_validate({
                "name": name,
                "value": _normalize_value(name, raw.get("value")),
                "source": {
                    "document": src.get("document") or default_doc,
                    "page": src.get("page"),
                    "quote": src.get("quote"),
                },
            })
        except ValidationError as exc:
            err = exc.errors()[0]
            result.rejected.append(f"fact #{index} ({name}): {err['loc']} {err['msg']}")
            continue

        check = _check(by_id, fact)
        result.facts.append(fact)
        result.citation_checks[name] = check
        if not check.verified:
            result.needs_review.append(name)

    return result
