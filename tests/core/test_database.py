"""Runs against the real local PostgreSQL instance (see core/database),
same approach as the OCR tests running against the real Tesseract binary.
Each test cleans up its own rows."""

from __future__ import annotations

import uuid

import pytest

from core.database.models import Company, Document
from core.database.session import init_db, session_scope


@pytest.fixture(autouse=True, scope="module")
def _ensure_schema() -> None:
    init_db()


def test_insert_and_query_document() -> None:
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    doc_id = str(uuid.uuid4())

    with session_scope() as session:
        session.add(Company(id=company_id, display_name="Test Co"))
        session.add(
            Document(
                id=doc_id,
                company_id=company_id,
                original_file_name="101.pdf",
                file_path=f"health/{doc_id}.pdf",
                domain="health",
                appendix_number=["101"],
                appendix_name="כיסוי לדוגמה",
                extraction_method="ocr",
            )
        )

    with session_scope() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        assert document.appendix_number == ["101"]
        assert document.company.display_name == "Test Co"

    with session_scope() as session:
        session.delete(session.get(Document, doc_id))
        session.delete(session.get(Company, company_id))


def test_appendix_number_supports_multiple_values() -> None:
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    doc_id = str(uuid.uuid4())

    with session_scope() as session:
        session.add(Company(id=company_id, display_name="Test Co"))
        session.add(
            Document(
                id=doc_id,
                company_id=company_id,
                original_file_name="bundle.pdf",
                file_path=f"health/{doc_id}.pdf",
                domain="health",
                appendix_number=["101", "102"],
                extraction_method="text",
            )
        )

    with session_scope() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        assert document.appendix_number == ["101", "102"]
        session.delete(document)
        session.delete(session.get(Company, company_id))
