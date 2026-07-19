"""Runs against the real local PostgreSQL instance, same approach as
test_database.py. Each test cleans up its own rows."""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from core.database.models import (
    Company,
    Document,
    DocumentEmbedding,
    DocumentExtraction,
    DocumentMatch,
)
from core.database.session import init_db, session_scope
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _ensure_schema() -> None:
    init_db()


@pytest.fixture
def two_documents() -> Generator[list[str], None, None]:
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    doc_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    with session_scope() as session:
        session.add(Company(id=company_id, display_name="Test Co"))
        for doc_id in doc_ids:
            session.add(
                Document(
                    id=doc_id,
                    company_id=company_id,
                    original_file_name=f"{doc_id}.pdf",
                    file_path=f"health/{doc_id}.pdf",
                    domain="health",
                    appendix_number=["101"],
                    extraction_method="text",
                )
            )

    yield doc_ids

    with session_scope() as session:
        session.query(DocumentMatch).filter(DocumentMatch.document_id.in_(doc_ids)).delete(
            synchronize_session=False
        )
        session.query(DocumentEmbedding).filter(
            DocumentEmbedding.document_id.in_(doc_ids)
        ).delete(synchronize_session=False)
        session.query(DocumentExtraction).filter(
            DocumentExtraction.document_id.in_(doc_ids)
        ).delete(synchronize_session=False)
        for doc_id in doc_ids:
            document = session.get(Document, doc_id)
            if document is not None:
                session.delete(document)
        session.delete(session.get(Company, company_id))


def test_list_documents_reports_extraction_and_embedding_flags(
    two_documents: list[str],
) -> None:
    doc_a, doc_b = two_documents
    with session_scope() as session:
        session.add(DocumentExtraction(document_id=doc_a, coverage_type="ביטוח בריאות"))
        session.add(DocumentEmbedding(document_id=doc_a, embedding=[0.1, 0.2], model_name="test"))

    response = client.get("/api/documents")
    assert response.status_code == 200
    by_id = {d["id"]: d for d in response.json()}

    assert by_id[doc_a]["has_extraction"] is True
    assert by_id[doc_a]["has_embedding"] is True
    assert by_id[doc_b]["has_extraction"] is False
    assert by_id[doc_b]["has_embedding"] is False


def test_get_extraction_returns_404_when_missing(two_documents: list[str]) -> None:
    response = client.get(f"/api/extractions/{two_documents[0]}")
    assert response.status_code == 404


def test_get_extraction_returns_stored_fields(two_documents: list[str]) -> None:
    doc_a = two_documents[0]
    with session_scope() as session:
        session.add(
            DocumentExtraction(
                document_id=doc_a,
                coverage_type="ביטוח בריאות",
                exclusions=["מחלה קודמת"],
                tables={"tables": [{"title": "t", "headers": ["a"], "rows": [["1"]]}]},
            )
        )

    response = client.get(f"/api/extractions/{doc_a}")
    assert response.status_code == 200
    body = response.json()
    assert body["coverage_type"] == "ביטוח בריאות"
    assert body["exclusions"] == ["מחלה קודמת"]
    assert body["tables"][0]["headers"] == ["a"]


def test_get_extraction_handles_document_ids_containing_slashes() -> None:
    """Real document IDs look like "phoenix:phoenix/health/x.pdf" - the
    route must accept literal "/" in the path, not just plain UUIDs."""
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    document_id = f"{company_id}:{company_id}/health/{uuid.uuid4().hex}.pdf"

    with session_scope() as session:
        session.add(Company(id=company_id, display_name="Test Co"))
        session.add(
            Document(
                id=document_id,
                company_id=company_id,
                original_file_name="x.pdf",
                file_path=f"health/{document_id}.pdf",
                domain="health",
                extraction_method="text",
            )
        )

    with session_scope() as session:
        session.add(DocumentExtraction(document_id=document_id, coverage_type="ביטוח חיים"))

    try:
        response = client.get(f"/api/extractions/{document_id}")
        assert response.status_code == 200
        assert response.json()["coverage_type"] == "ביטוח חיים"
    finally:
        with session_scope() as session:
            session.query(DocumentExtraction).filter_by(document_id=document_id).delete()
            session.delete(session.get(Document, document_id))
            session.delete(session.get(Company, company_id))


def test_list_matches_filters_by_status(two_documents: list[str]) -> None:
    doc_a, doc_b = two_documents
    with session_scope() as session:
        session.add(
            DocumentMatch(
                id=f"{doc_a}:{doc_b}",
                document_id=doc_a,
                matched_document_id=doc_b,
                similarity_score=0.97,
                status="auto_confirmed",
            )
        )

    auto_confirmed = client.get("/api/matches", params={"status": "auto_confirmed"}).json()
    pending = client.get("/api/matches", params={"status": "pending_review"}).json()

    matched_ids = {m["id"] for m in auto_confirmed}
    assert f"{doc_a}:{doc_b}" in matched_ids
    assert all(m["document"]["id"] != doc_a for m in pending if m["id"] == f"{doc_a}:{doc_b}")
