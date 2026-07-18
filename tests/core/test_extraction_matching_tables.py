"""Runs against the real local PostgreSQL instance, same approach as
test_database.py. Each test cleans up its own rows."""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest

from core.database.models import (
    Company,
    Document,
    DocumentEmbedding,
    DocumentExtraction,
    DocumentMatch,
)
from core.database.session import init_db, session_scope


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


def test_document_extraction_round_trip(two_documents: list[str]) -> None:
    document_id = two_documents[0]
    with session_scope() as session:
        session.add(
            DocumentExtraction(
                document_id=document_id,
                coverage_type="ביטוח בריאות",
                insurance_amounts=["500000"],
                exclusions=["מחלה קודמת"],
                tables={"tables": [{"title": "t", "headers": ["a"], "rows": [["1"]]}]},
                raw_extraction={"coverage_type": "ביטוח בריאות"},
            )
        )

    with session_scope() as session:
        row = session.query(DocumentExtraction).filter_by(document_id=document_id).one()
        assert row.coverage_type == "ביטוח בריאות"
        assert row.exclusions == ["מחלה קודמת"]
        assert row.tables["tables"][0]["headers"] == ["a"]


def test_document_embedding_round_trip(two_documents: list[str]) -> None:
    document_id = two_documents[0]
    with session_scope() as session:
        session.add(
            DocumentEmbedding(
                document_id=document_id,
                embedding=[0.1, 0.2, 0.3],
                model_name="intfloat/multilingual-e5-large",
            )
        )

    with session_scope() as session:
        row = session.get(DocumentEmbedding, document_id)
        assert row is not None
        assert row.embedding == [0.1, 0.2, 0.3]


def test_document_match_round_trip(two_documents: list[str]) -> None:
    doc_a, doc_b = two_documents
    match_id = f"{doc_a}:{doc_b}"
    with session_scope() as session:
        session.add(
            DocumentMatch(
                id=match_id,
                document_id=doc_a,
                matched_document_id=doc_b,
                similarity_score=0.97,
                status="auto_confirmed",
            )
        )

    with session_scope() as session:
        row = session.get(DocumentMatch, match_id)
        assert row is not None
        assert row.similarity_score == pytest.approx(0.97)
        assert row.status == "auto_confirmed"
