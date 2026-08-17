"""Response models for the dashboard API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    company_id: str
    domain: str
    original_file_name: str
    appendix_number: list[str]
    appendix_name: str | None
    pages_count: int | None
    extraction_method: str
    has_extraction: bool
    has_embedding: bool
    # Marketing validity window (populated for most companies now, via each
    # company's downloader - NULL for a company that doesn't publish one,
    # which reads as "always active"). Every document is always listed here
    # regardless of is_active - this is the view/download listing, not the
    # matching candidate pool; only scripts/match_documents.py filters by it.
    marketing_start_date: date | None
    marketing_end_date: date | None
    is_active: bool
    # Taxonomy classification (core/taxonomy/, scripts/classify_documents.py) -
    # None until the document has been classified. Read-only here like
    # everything else in this module: no LLM call happens on request.
    category_id: str | None
    main_category: str | None
    coverage_family: str | None
    coverage_subtype: str | None
    created_date: datetime


class PolicyTableOut(BaseModel):
    title: str | None
    headers: list[str]
    rows: list[list[str]]


class ExtractionOut(BaseModel):
    document_id: str
    appendix_number: list[str]
    appendix_name: str | None
    coverage_type: str | None
    coverage_name: str | None
    eligibility_conditions: str | None
    insurance_amounts: list[str]
    qualifying_period: str | None
    waiting_period: str | None
    exclusions: list[str]
    age_range: str | None
    restrictions: list[str]
    tables: list[PolicyTableOut]
    disease_count: int | None
    disease_list: list[str]
    survival_period: str | None
    created_date: datetime


class MatchDocumentSummary(BaseModel):
    id: str
    company_id: str
    domain: str
    original_file_name: str
    appendix_number: list[str]
    appendix_name: str | None


class MatchOut(BaseModel):
    id: str
    document: MatchDocumentSummary
    matched_document: MatchDocumentSummary
    similarity_score: float
    status: str
    created_date: datetime


class MatchStatusUpdate(BaseModel):
    status: Literal["confirmed", "rejected"]


class ClassificationOut(BaseModel):
    category_id: str
    main_category: str
    coverage_family: str
    coverage_subtype: str | None
    coverage_variant: str | None
    benefit_model: str | None
    target_population: str | None
    confidence: float | None
    evidence: str | None


class CanonicalProfileOut(BaseModel):
    insured_event: str | None
    covered_events: list[str]
    covered_conditions: list[str]
    exclusions_normalized: list[str]
    limitations: list[str]
    eligibility_normalized: str | None
    waiting_period_text: str | None = None
    qualifying_period_text: str | None = None
    survival_period_text: str | None = None
    benefit_type: str | None
    benefit_calculation: str | None
    amounts: list[str]
    caps: list[str]
    additional_findings_summary: str | None


class FingerprintOut(BaseModel):
    waiting_period_days: int | None
    qualifying_period_days: int | None
    survival_period_days: int | None
    min_entry_age: int | None
    max_entry_age: int | None
    termination_age: int | None
    benefit_type: str | None
    benefit_amount_min: float | None
    benefit_amount_max: float | None
    benefit_amount_currency: str | None
    benefit_percentage: float | None
    maximum_benefit: float | None
    deductible_amount: float | None
    covered_event_count: int
    major_exclusion_count: int
    special_condition_count: int


class DocumentAnalysisOut(BaseModel):
    """Everything the taxonomy/matching upgrade has computed for one
    document so far - each section is None until its pipeline stage has
    actually run for this document (see core/database/models.py's
    DocumentPipelineStatus), never guessed or backfilled on request."""

    document_id: str
    classification: ClassificationOut | None
    canonical_profile: CanonicalProfileOut | None
    fingerprint: FingerprintOut | None
