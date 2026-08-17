"""SQLAlchemy ORM models.

Scoped to what Stage 4 actually needs: a Company row per plugin and a
Document row per processed file, holding the identity fields the
extraction pipeline produces (filename, appendix number(s), domain).
Policies/Appendices/OCR_Results/Extracted_Text/Processing_Logs tables
from the original spec are deferred until a company/stage actually
produces data for them — no point in empty tables.

DocumentExtraction/DocumentEmbedding/DocumentMatch (added next) support
cross-company appendix matching: structured fields pulled from each
document's text via LLM, an embedding of those fields (not the raw
appendix number, which differs per company), and the resulting
best-match pairing between companies' documents.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "migdal"
    display_name: Mapped[str] = mapped_column(String, nullable=False)

    documents: Mapped[list[Document]] = relationship(back_populates="company")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)

    original_file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)  # health | life | mixed

    appendix_number: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    appendix_name: Mapped[str | None] = mapped_column(String, nullable=True)
    department_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # Marketing validity window, when the source company's site publishes
    # one (currently: Harel's "תאריך תחילת/סיום שיווק" archive columns) -
    # NULL for every other company, which is read as "no signal, treat as
    # currently active" everywhere this is used (see Document.is_active).
    marketing_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    marketing_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    pages_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String, nullable=False)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="documents")

    @property
    def is_active(self) -> bool:
        """Currently marketed, per marketing_end_date - True whenever the
        source company gives no marketing-validity signal at all (NULL),
        same default every other company implicitly gets."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()


class DocumentExtraction(Base):
    """Structured fields pulled from one document's text via LLM.

    Dedicated columns hold the fields that matter for cross-company
    comparison; `tables`/`raw_extraction` are JSONB catch-alls for
    structure (tables) and the full model output (auditability).
    """

    __tablename__ = "document_extractions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"), nullable=False, unique=True
    )

    coverage_type: Mapped[str | None] = mapped_column(String, nullable=True)
    coverage_name: Mapped[str | None] = mapped_column(String, nullable=True)
    eligibility_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    insurance_amounts: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    qualifying_period: Mapped[str | None] = mapped_column(String, nullable=True)
    waiting_period: Mapped[str | None] = mapped_column(String, nullable=True)
    exclusions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    age_range: Mapped[str | None] = mapped_column(String, nullable=True)
    restrictions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    tables: Mapped[dict] = mapped_column(JSONB, default=dict)
    disease_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disease_list: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    survival_period: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_extraction: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentEmbedding(Base):
    """Embedding of a document's extracted fields (not its appendix number)."""

    __tablename__ = "document_embeddings"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentMatch(Base):
    """A candidate cross-company match between two documents' content.

    `similarity_score`/`status` are the original, always-populated fields -
    api/routes.py and the frontend read only these and need no changes.
    Everything below them is additive: nullable columns written by the new
    quantitative matching engine (core/matching/orchestrator.py,
    scripts/match_documents_v2.py) so a match's score is explainable
    instead of a bare float, without touching the columns above.
    """

    __tablename__ = "document_matches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    matched_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # --- additive columns (Phase 4 quantitative matching engine) ---
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    critical_mismatches: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    material_differences: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    missing_features: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    best_candidate_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    second_candidate_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    mutual_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    group_validation_status: Mapped[str | None] = mapped_column(String, nullable=True)
    match_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    hard_constraint_failures: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    matching_profile_version: Mapped[str | None] = mapped_column(String, nullable=True)
    auditor_verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    auditor_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentClassification(Base):
    """Taxonomy classification of a document (core/taxonomy/), 1:1 with Document.

    Complements DocumentExtraction/PolicyExtraction - does not replace or
    duplicate any of its fields.
    """

    __tablename__ = "document_classifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"), nullable=False, unique=True
    )

    taxonomy_version: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[str] = mapped_column(String, nullable=False)
    main_category: Mapped[str] = mapped_column(String, nullable=False)
    coverage_family: Mapped[str] = mapped_column(String, nullable=False)
    coverage_subtype: Mapped[str | None] = mapped_column(String, nullable=True)
    coverage_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    benefit_model: Mapped[str | None] = mapped_column(String, nullable=True)
    target_population: Mapped[str | None] = mapped_column(String, nullable=True)
    alternative_categories: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentQuestionAnswer(Base):
    """One question-bank answer for one document.

    Many rows per document (base questions + this document's category-
    specific questions). status distinguishes "explicitly absent from the
    document" (NOT_FOUND) from "doesn't apply to this coverage type"
    (NOT_APPLICABLE) from "the model couldn't answer confidently"
    (AMBIGUOUS) - never collapsed into a single "no answer".
    """

    __tablename__ = "document_question_answers"
    __table_args__ = (
        Index(
            "ix_document_question_answers_unique",
            "document_id",
            "question_bank_version",
            "question_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)

    question_bank_version: Mapped[str] = mapped_column(String, nullable=False)
    question_id: Mapped[str] = mapped_column(String, nullable=False)
    question_scope: Mapped[str] = mapped_column(String, nullable=False)  # base | category

    status: Mapped[str] = mapped_column(String, nullable=False)  # FOUND | NOT_FOUND | NOT_APPLICABLE | AMBIGUOUS
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_section: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_response: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentAdditionalFinding(Base):
    """Material information found in a document that doesn't fit the existing
    schema or the question bank - many rows per document."""

    __tablename__ = "document_additional_findings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)

    finding_text: Mapped[str] = mapped_column(Text, nullable=False)
    related_field: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_page: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentCanonicalProfile(Base):
    """Canonical Coverage Profile: a company-phrasing-independent summary of
    what a document actually covers, 1:1 with Document. Built from
    DocumentExtraction + DocumentQuestionAnswer + DocumentAdditionalFinding,
    none of which it replaces."""

    __tablename__ = "document_canonical_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"), nullable=False, unique=True
    )

    profile_version: Mapped[str] = mapped_column(String, nullable=False)
    insured_event: Mapped[str | None] = mapped_column(Text, nullable=True)
    covered_events: Mapped[list] = mapped_column(JSONB, default=list)
    covered_conditions: Mapped[list] = mapped_column(JSONB, default=list)
    exclusions_normalized: Mapped[list] = mapped_column(JSONB, default=list)
    limitations: Mapped[list] = mapped_column(JSONB, default=list)
    eligibility_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    waiting_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualifying_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    survival_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    benefit_type: Mapped[str | None] = mapped_column(String, nullable=True)
    benefit_calculation: Mapped[str | None] = mapped_column(Text, nullable=True)
    amounts: Mapped[list] = mapped_column(JSONB, default=list)
    caps: Mapped[list] = mapped_column(JSONB, default=list)
    deductible: Mapped[dict] = mapped_column(JSONB, default=dict)
    age_restrictions: Mapped[dict] = mapped_column(JSONB, default=dict)
    pre_existing_condition_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_requirements: Mapped[list] = mapped_column(JSONB, default=list)
    definitions: Mapped[dict] = mapped_column(JSONB, default=dict)
    extensions: Mapped[list] = mapped_column(JSONB, default=list)
    special_conditions: Mapped[list] = mapped_column(JSONB, default=list)
    termination_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_findings_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full model output, same audit-copy convention as
    # DocumentExtraction.raw_extraction.
    raw_profile: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentCanonicalCode(Base):
    """One normalized canonical-code membership for a document - many rows per
    document, one per covered event/exclusion/eligibility condition/etc that
    was successfully mapped to a code in core/knowledge_base/data/canonical_codes.*.yaml.

    This is the set data quantitative matching's Jaccard/weighted-Jaccard
    similarity (core/matching/quantitative_score.py) operates on - kept
    separate from DocumentFingerprint's scalar/numeric columns.
    """

    __tablename__ = "document_canonical_codes"
    __table_args__ = (
        Index("ix_document_canonical_codes_doc_category", "document_id", "code_category"),
        Index("ix_document_canonical_codes_category_code", "code_category", "code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)

    code_category: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    raw_phrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_field: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentFingerprint(Base):
    """Quantitative Insurance Fingerprint: the scalar/enum/numeric features of
    a document, derived purely from code (DocumentCanonicalProfile +
    DocumentCanonicalCode + DocumentQuestionAnswer) with no LLM call of its
    own - deterministic and re-buildable at will. 1:1 with Document.

    Set-valued features (covered_event_codes[] etc) live in
    DocumentCanonicalCode, not duplicated here.
    """

    __tablename__ = "document_fingerprints"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"), nullable=False, unique=True
    )

    fingerprint_version: Mapped[str] = mapped_column(String, nullable=False)
    main_category: Mapped[str | None] = mapped_column(String, nullable=True)
    coverage_family: Mapped[str | None] = mapped_column(String, nullable=True)
    coverage_subtype: Mapped[str | None] = mapped_column(String, nullable=True)
    benefit_model: Mapped[str | None] = mapped_column(String, nullable=True)
    target_population: Mapped[str | None] = mapped_column(String, nullable=True)

    waiting_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    waiting_period_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    qualifying_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualifying_period_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    survival_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    survival_period_raw: Mapped[str | None] = mapped_column(String, nullable=True)

    min_entry_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_entry_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    termination_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_raw: Mapped[str | None] = mapped_column(String, nullable=True)

    benefit_type: Mapped[str | None] = mapped_column(String, nullable=True)
    benefit_amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    benefit_amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    benefit_amount_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    amount_raw: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    benefit_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum_benefit: Mapped[float | None] = mapped_column(Float, nullable=True)
    deductible_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    covered_event_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    major_exclusion_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    special_condition_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    raw_features: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentLayeredEmbedding(Base):
    """One embedding per (document, semantic layer) - additive to, not a
    replacement of, DocumentEmbedding's single whole-document vector. Used
    only for candidate retrieval (core/matching/layered_embeddings.py),
    never as the final match decision."""

    __tablename__ = "document_layered_embeddings"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    # summary | coverage | insured_event | definitions | exclusions |
    # eligibility | benefit_structure | canonical_profile
    layer: Mapped[str] = mapped_column(String, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentPipelineStatus(Base):
    """Per-document, per-stage cache ledger: what ran, at which config
    version, and when. Every new pipeline script checks this table before
    making an LLM call, so a document already processed at the current
    config version is never re-analyzed - this is what makes "a new
    document only touches its own category, never the whole corpus"
    mechanically enforceable rather than just a convention."""

    __tablename__ = "document_pipeline_status"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)

    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String, nullable=True)

    questions_answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    question_bank_version: Mapped[str | None] = mapped_column(String, nullable=True)

    canonical_profile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    profile_version: Mapped[str | None] = mapped_column(String, nullable=True)

    canonical_codes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canonical_codes_version: Mapped[str | None] = mapped_column(String, nullable=True)

    fingerprint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fingerprint_version: Mapped[str | None] = mapped_column(String, nullable=True)

    layered_embeddings_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    embedding_model_name: Mapped[str | None] = mapped_column(String, nullable=True)


class MatchCalibrationRun(Base):
    """Audit trail of a calibration analysis run (scripts/calibrate_matching.py)
    over the existing, human-reviewed document_matches - never a source of
    truth itself, just a record of what was proposed and why. A human
    reviews the proposal and hand-commits it into
    core/matching/profiles/data/{category}.v*.yaml."""

    __tablename__ = "match_calibration_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_importance: Mapped[dict] = mapped_column(JSONB, default=dict)
    hard_constraints_proposed: Mapped[list] = mapped_column(JSONB, default=list)
    weights_proposed: Mapped[dict] = mapped_column(JSONB, default=dict)
    thresholds_proposed: Mapped[dict] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_version_written: Mapped[str | None] = mapped_column(String, nullable=True)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
