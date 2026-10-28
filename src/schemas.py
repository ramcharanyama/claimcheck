"""Shared Pydantic Schemas for ClaimCheck 2.0.

Defines the contract for source references, rules, rulebooks, claim facts,
findings, and evaluation scenarios.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class RuleType(str, Enum):
    """Permitted rule types for policy clauses."""
    REQUIRED_DOCUMENTS = "required_documents"
    WAITING_PERIOD = "waiting_period"
    ROOM_RENT_CAP = "room_rent_cap"
    SUB_LIMIT = "sub_limit"
    NON_PAYABLE_ITEMS = "non_payable_items"
    PRE_HOSPITALIZATION = "pre_hospitalization"
    POST_HOSPITALIZATION = "post_hospitalization"
    AMBULANCE_LIMIT = "ambulance_limit"
    SUM_INSURED = "sum_insured"
    CASHLESS_PREAUTHORIZATION = "cashless_preauthorization"
    PRE_EXISTING_DISCLOSURE = "pre_existing_disclosure"


class ValidationStatus(str, Enum):
    """Explicit lifecycle status for rule validation."""
    DRAFT = "draft"
    VALIDATED = "validated"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class FindingStatus(str, Enum):
    """Permitted evaluation outcome statuses for findings."""
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNABLE_TO_VERIFY = "unable_to_verify"
    NEEDS_REVIEW = "needs_review"


class SourceRef(BaseModel):
    """Source reference pointing to a specific document, 1-based page, and verbatim quote."""
    model_config = ConfigDict(extra="forbid")

    document: str = Field(..., min_length=1, description="Identifier or filename of source document.")
    page: int = Field(..., ge=1, description="1-based page number where the quotation originates.")
    quote: str = Field(..., min_length=1, description="Verbatim text quotation from the source document.")


class Rule(BaseModel):
    """Structured policy rule definition."""
    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    rule_id: str = Field(..., min_length=1, description="Unique identifier for the rule.")
    rule_type: RuleType = Field(..., description="Categorical type of the policy rule.")
    description: str = Field(..., min_length=1, description="Human-readable explanation of the rule.")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Explicit parameters governing rule evaluation.")
    exceptions: List[str] = Field(default_factory=list, description="Explicit conditions where rule does not apply.")
    source: SourceRef = Field(..., description="Source citation with page number and verbatim quote.")
    validation_status: ValidationStatus = Field(
        default=ValidationStatus.VALIDATED,
        description="Explicit validation status (e.g. validated, draft)."
    )
    human_review_required: bool = Field(
        default=False,
        description="Flag indicating whether human adjuster adjudication is mandatory."
    )


class Ambiguity(BaseModel):
    """Reference to an identified policy ambiguity requiring external interpretation or review."""
    model_config = ConfigDict(extra="forbid")

    ambiguity_id: str = Field(..., min_length=1, description="Unique identifier for the ambiguity.")
    section: str = Field(..., min_length=1, description="Policy section containing the ambiguity.")
    description: str = Field(..., min_length=1, description="Description of the ambiguous clause.")
    issue: str = Field(..., min_length=1, description="Core issue or missing definition causing ambiguity.")
    source: SourceRef = Field(..., description="Source citation with page number and verbatim quote.")


# Backward compatibility alias
AmbiguityRef = Ambiguity


class Rulebook(BaseModel):
    """Collection of rules governing an insurance policy."""
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(..., min_length=1, description="Unique policy identifier.")
    rules: List[Rule] = Field(..., min_length=1, description="List of validated policy rules.")
    ambiguities: List[Ambiguity] = Field(
        default_factory=list,
        description="List of identified ambiguous policy clauses requiring human review or external context."
    )


class ClaimFact(BaseModel):
    """Individual factual assertion extracted from a claim or hospital document."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Identifier of the claimed fact (e.g. 'submitted_documents').")
    value: Any = Field(..., description="Value of the claim fact (primitive, list, or mapping).")
    source: SourceRef = Field(..., description="Verifiable document source citation.")


class Finding(BaseModel):
    """Outcome of evaluating a rule against claim facts."""
    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    rule_id: str = Field(..., min_length=1, description="ID of the evaluated rule.")
    status: FindingStatus = Field(..., description="Outcome status: satisfied, violated, unable_to_verify, needs_review.")
    inputs_used: Union[Dict[str, Any], List[str]] = Field(
        ...,
        description="Claim facts and values utilized in evaluating this rule."
    )
    calculation_steps: List[str] = Field(
        default_factory=list,
        description="Step-by-step arithmetic or logical deduction trace."
    )
    explanation: str = Field(..., min_length=1, description="Detailed reasoning for the finding outcome.")
    source: SourceRef = Field(..., description="Citation to the governing policy clause.")
    estimated_impact: Optional[float] = Field(
        default=None,
        description="Deterministic financial impact (deduction/inadmissible amount) if fully determined."
    )


class ClaimScenario(BaseModel):
    """Deterministic claim test scenario for automated verification."""
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier.")
    description: str = Field(..., min_length=1, description="Summary of the scenario objective.")
    claim_facts: List[ClaimFact] = Field(default_factory=list, description="Factual inputs extracted from claim.")
    applicable_rule_ids: List[str] = Field(..., min_length=1, description="Rule IDs relevant to this scenario.")
    expected_findings: List[Finding] = Field(..., min_length=1, description="Expected findings for applicable rules.")
    missing_information_required: bool = Field(
        default=False,
        description="Whether missing claim facts prevent conclusive automated determination."
    )
    human_review_required: bool = Field(
        default=False,
        description="Whether human adjuster intervention is required."
    )
    expected_financial_impact: Optional[float] = Field(
        default=None,
        description="Expected total financial deduction directly supported by policy and known facts."
    )
