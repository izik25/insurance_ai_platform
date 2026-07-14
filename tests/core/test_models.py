from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from core.models.document import DocumentIdentity
from core.models.enums import DocumentType, ExtractionMethod


def test_minimal_required_fields() -> None:
    doc = DocumentIdentity(
        company="Harel",
        document_type=DocumentType.APPENDIX,
        original_file_name="4521.pdf",
        file_path="/documents/harel/4521.pdf",
        extraction_method=ExtractionMethod.OCR,
    )
    assert doc.company == "Harel"
    assert doc.policy_number is None
    assert isinstance(doc.document_id, str) and doc.document_id
    assert isinstance(doc.created_date, datetime)


def test_full_payload_round_trip() -> None:
    payload = {
        "company": "Harel",
        "policy_number": "123456",
        "appendix_number": "4521",
        "appendix_name": "כיסוי אובדן כושר עבודה",
        "document_type": "appendix",
        "original_file_name": "4521.pdf",
        "file_path": "/documents/harel/4521.pdf",
        "pages_count": 3,
        "extraction_method": "ocr",
        "ocr_confidence": 0.97,
    }
    doc = DocumentIdentity(**payload)
    dumped = doc.model_dump()
    assert dumped["policy_number"] == "123456"
    assert dumped["appendix_name"] == "כיסוי אובדן כושר עבודה"
    assert dumped["ocr_confidence"] == 0.97


def test_ocr_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        DocumentIdentity(
            company="Harel",
            document_type=DocumentType.APPENDIX,
            original_file_name="x.pdf",
            file_path="/x.pdf",
            extraction_method=ExtractionMethod.OCR,
            ocr_confidence=1.5,
        )
