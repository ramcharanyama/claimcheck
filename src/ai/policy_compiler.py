"""Policy Compiler for ClaimCheck 2.0.

Turns extracted policy pages into structured Rule objects using NVIDIA Nemotron
via Nebius Token Factory, then verifies every citation against the PDF text.

AI-generated rules are always drafts. Any rule whose quote is not found on its
cited page is flagged human_review_required=True.
"""

from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel, Field, ValidationError

from src.ai.nebius_client import chat_json, get_client, get_model
from src.documents.evidence import CitationCheck, check_citation
from src.documents.pdf_extractor import DocumentExtraction
from src.schemas import Ambiguity, Rule, Rulebook, RuleType, ValidationStatus


class PolicyCompilerError(Exception):
    """Raised when the model response does not contain a rules list."""


class CompilationResult(BaseModel):
    policy_id: str
    model: str
    rules: List[Rule] = Field(default_factory=list)
    citation_checks: Dict[str, CitationCheck] = Field(default_factory=dict)
    rejected: List[str] = Field(default_factory=list)
    ambiguities: List[Ambiguity] = Field(default_factory=list)
    ambiguity_checks: Dict[str, CitationCheck] = Field(default_factory=dict)

    @property
    def verified_count(self) -> int:
        return sum(c.verified for c in self.citation_checks.values())

    def to_rulebook(self) -> Rulebook:
        return Rulebook(policy_id=self.policy_id, rules=self.rules, ambiguities=self.ambiguities)


SYSTEM_PROMPT = (
    "You are an insurance policy analyst. Extract machine-checkable rules from policy text. "
    "Use only what the policy states. Never invent limits, amounts, or exceptions. "
    "Every rule must cite one page and copy its quote from that page word for word. "
    "Respond with a single JSON object and nothing else."
)

PARAMETER_SCHEMA = """Use exactly these parameter keys for each rule type. Take every value from the policy text:
- required_documents: {"required_documents": [document names], "minimum_document_count": integer}
- waiting_period: {"duration_days": integer, "applies_to": category the waiting period covers, "reference_date": the date it counts from}
- room_rent_cap: {"cap": number, "daily_limit_inr": same number as cap, "currency": "INR", "unit": "per_day", "excess_payable_by": who pays any excess, "formula_basis": the payable ratio for other charges written as an expression using the policy's own numbers and actual_daily_rate for the billed daily room rate, or null if the policy states no such ratio, "affected_categories": [charge categories that ratio applies to] or []}
  For room_rent_cap, quote the whole clause, including any sentence about a ratio or proportional deduction.
- sub_limit: {"category": procedure name, "max_limit_inr": number, "currency": "INR", "unit": what the limit is counted per, "inclusive_items": [items the limit covers]}
- non_payable_items: {"excluded_items": [item names]}
- pre_hospitalization: {"window_days": integer, "reference_date": "admission_date"}
- post_hospitalization: {"window_days": integer, "reference_date": "discharge_date"}
- ambulance_limit: {"max_limit_inr": number, "currency": "INR", "unit": what the limit is counted per}
- sum_insured: {"sum_insured_inr": number, "currency": "INR", "unit": the period it applies to}
- cashless_preauthorization: {"required_documents": [document names], "planned_notice_hours_before": integer, "emergency_notice_hours_after": integer}
- pre_existing_disclosure: {"action_if_undeclared": what happens to an undeclared condition, in snake_case}
Only create a rule of a type above if the policy actually contains such a clause.
Write every name and list item in lowercase snake_case, for example "face_masks", "discharge_summary", "policy_inception_date"."""


USER_TEMPLATE = """Policy document: {document_id}

Extract every rule of these types: {rule_types}.

Return JSON in exactly this shape:
{{"rules": [{{
  "rule_id": "RULE_<SHORT_NAME>",
  "rule_type": "<one of the types above>",
  "description": "<one sentence>",
  "parameters": {{<explicit values from the text, e.g. "duration_days": 30>}},
  "exceptions": ["<exceptions explicitly stated in the text>"],
  "source": {{"page": <page number>, "quote": "<exact sentence(s) copied from that page>"}}
}}],
 "ambiguities": [{{
  "ambiguity_id": "AMB_<SHORT_NAME>",
  "section": "<section number as written, e.g. Section 9.9>",
  "description": "<what is unclear and why>",
  "issue": "<the missing definition or reference, in a few words>",
  "source": {{"page": <page number>, "quote": "<exact sentence(s) copied from that page>"}}
}}]}}

{parameter_schema}

Ambiguities: list every clause that affects coverage but cannot be applied from this document alone, for
example because it relies on a term, regulation, period or amount the policy refers to but never defines.
Do not turn such a clause into a rule. Use an empty list if there are none.

Policy text, page by page:
{pages}"""


def _format_pages(extraction: DocumentExtraction) -> str:
    return "\n\n".join(f"=== PAGE {p.page_number} ===\n{p.text.strip()}" for p in extraction.pages)


def _build_rule(raw, document_id: str, result: CompilationResult, index: int):
    if not isinstance(raw, dict):
        result.rejected.append(f"rule #{index}: not a JSON object")
        return None
    src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    try:
        return Rule.model_validate({
            "rule_id": raw.get("rule_id"),
            "rule_type": raw.get("rule_type"),
            "description": raw.get("description"),
            "parameters": raw.get("parameters") or {},
            "exceptions": raw.get("exceptions") or [],
            "source": {"document": document_id, "page": src.get("page"), "quote": src.get("quote")},
            "validation_status": ValidationStatus.DRAFT,
            "human_review_required": True,
        })
    except ValidationError as exc:
        err = exc.errors()[0]
        result.rejected.append(f"rule #{index} ({raw.get('rule_id')}): {err['loc']} {err['msg']}")
        return None


def compile_policy_rules(
    extraction: DocumentExtraction,
    policy_id: str,
    client=None,
    model: str | None = None,
) -> CompilationResult:
    """Ask Nemotron for rules, validate them, and verify every citation."""
    client = client or get_client()
    model = model or get_model("reasoning")

    user = USER_TEMPLATE.format(
        document_id=extraction.document_id,
        rule_types=", ".join(t.value for t in RuleType),
        parameter_schema=PARAMETER_SCHEMA,
        pages=_format_pages(extraction),
    )
    data = chat_json(client, model, SYSTEM_PROMPT, user)

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise PolicyCompilerError("Model response has no 'rules' list.")

    result = CompilationResult(policy_id=policy_id, model=model)
    seen = set()
    for index, raw in enumerate(raw_rules, start=1):
        rule = _build_rule(raw, extraction.document_id, result, index)
        if rule is None:
            continue
        if rule.rule_id in seen:
            result.rejected.append(f"rule #{index}: duplicate rule_id {rule.rule_id}")
            continue
        seen.add(rule.rule_id)

        check = check_citation(extraction, rule.source)
        result.rules.append(rule.model_copy(update={"human_review_required": not check.verified}))
        result.citation_checks[rule.rule_id] = check

    raw_ambiguities = data.get("ambiguities") or []
    if not isinstance(raw_ambiguities, list):
        result.rejected.append("'ambiguities' is not a list")
        raw_ambiguities = []
    for index, raw in enumerate(raw_ambiguities, start=1):
        amb = _build_ambiguity(raw, extraction.document_id, result, index)
        if amb is None:
            continue
        if any(a.ambiguity_id == amb.ambiguity_id for a in result.ambiguities):
            result.rejected.append(f"ambiguity #{index}: duplicate ambiguity_id {amb.ambiguity_id}")
            continue
        result.ambiguities.append(amb)
        result.ambiguity_checks[amb.ambiguity_id] = check_citation(extraction, amb.source)

    return result


def _build_ambiguity(raw, document_id: str, result: CompilationResult, index: int):
    """Validate one ambiguity. Ambiguities only ever lead to needs_review, so an
    unverified quote is kept (and reported in ambiguity_checks) rather than dropped."""
    if not isinstance(raw, dict):
        result.rejected.append(f"ambiguity #{index}: not a JSON object")
        return None
    src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    try:
        return Ambiguity.model_validate({
            "ambiguity_id": raw.get("ambiguity_id"),
            "section": raw.get("section"),
            "description": raw.get("description"),
            "issue": raw.get("issue"),
            "source": {"document": document_id, "page": src.get("page"), "quote": src.get("quote")},
        })
    except ValidationError as exc:
        err = exc.errors()[0]
        result.rejected.append(f"ambiguity #{index} ({raw.get('ambiguity_id')}): {err['loc']} {err['msg']}")
        return None
