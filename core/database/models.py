"""SQLAlchemy ORM models.

Scoped to what Stage 4 actually needs: a Company row per plugin and a
Document row per processed file, holding the identity fields the
extraction pipeline produces (filename, appendix number(s), domain).
Policies/Appendices/OCR_Results/Extracted_Text/Processing_Logs tables
from the original spec are deferred until a company/stage actually
produces data for them — no point in empty tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
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
