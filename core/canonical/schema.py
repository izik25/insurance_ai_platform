"""Pydantic schema for the LLM-facing Canonical Coverage Profile.

Deliberately avoids free-form dict fields (e.g. "definitions" as term ->
definition) because OpenAI's strict JSON Schema structured-output mode
needs a fixed key set per object - a free-form mapping is expressed here as
a list of small fixed-shape objects instead (DefinitionEntry), same trick
`PolicyExtraction.tables` already uses for tabular data.

Period fields are kept as TEXT here (e.g. "90 יום מיום ההצטרפות"), not
pre-converted to an integer day count by the LLM: turning a period string
into a trustworthy number is exactly the "Rule-Based Validation... periods"
requirement (req 22) - that conversion belongs to
core/fingerprint/parsers.py (Phase 3, pure code, no LLM), which is also why
DocumentCanonicalProfile.waiting_period_days/qualifying_period_days/
survival_period_days stay NULL out of this stage and only get populated by
the fingerprint builder into DocumentFingerprint.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Bumped whenever CanonicalCoverageProfile's shape or prompt changes in a
# way that should invalidate document_pipeline_status.canonical_profile_at
# for already-processed documents (same versioning convention as
# core/taxonomy and core/knowledge_base's *.v1.yaml files, just for a
# Python-defined schema instead of a YAML one).
PROFILE_VERSION = "v1"
CODES_VERSION = "v1"


class DefinitionEntry(BaseModel):
    term: str
    definition: str


class DeductibleInfo(BaseModel):
    applies: bool
    amount_text: str | None = None
    description: str | None = None


class AgeRestrictions(BaseModel):
    min_age: int | None = None
    max_age: int | None = None
    termination_age: int | None = None
    description: str | None = None


class CanonicalCoverageProfile(BaseModel):
    insured_event: str | None = None
    covered_events: list[str] = Field(default_factory=list)
    covered_conditions: list[str] = Field(default_factory=list)
    exclusions_normalized: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    eligibility_normalized: str | None = None
    waiting_period_text: str | None = None
    qualifying_period_text: str | None = None
    survival_period_text: str | None = None
    benefit_type: str | None = None
    benefit_calculation: str | None = None
    amounts: list[str] = Field(default_factory=list)
    caps: list[str] = Field(default_factory=list)
    deductible: DeductibleInfo
    age_restrictions: AgeRestrictions
    pre_existing_condition_rules: str | None = None
    claim_requirements: list[str] = Field(default_factory=list)
    definitions: list[DefinitionEntry] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    special_conditions: list[str] = Field(default_factory=list)
    termination_rules: str | None = None
    additional_findings_summary: str | None = None
