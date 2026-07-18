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
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
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

    pages_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String, nullable=False)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="documents")


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
    """A candidate cross-company match between two documents' content."""

    __tablename__ = "document_matches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    matched_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)

    created_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
