"""DocumentIdentity — the canonical identity record for every processed file.

Every stage of the pipeline (extraction, OCR, database, JSON dictionary,
RAG) reads and writes this shape. Getting this model right is the single
most important piece of the platform's data model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from core.models.enums import DocumentType, ExtractionMethod


class DocumentIdentity(BaseModel):
    """Canonical identity + provenance for a single processed document."""

    document_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    company: str
    policy_number: str | None = None
    appendix_number: str | None = None
    appendix_name: str | None = None
    document_type: DocumentType

    original_file_name: str
    file_path: str
    pages_count: int | None = Field(default=None, ge=0)

    extraction_method: ExtractionMethod
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    created_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
