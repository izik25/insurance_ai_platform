"""Runs against the real local PostgreSQL instance, same approach as
test_api_routes.py. Each test cleans up its own rows."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from core.database.models import Company, Document, DocumentExtraction, DocumentMatch
from core.database.session import init_db, session_scope
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def _ensure_schema() -> None:
    init_db()


@pytest.fixture
def appendix_document() -> Generator[tuple[str, str, str], None, None]:
    """Yields (company_id, appendix_number, document_id)."""
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    appendix_number = "205"
    document_id = str(uuid.uuid4())

    with session_scope() as session:
        session.add(Company(id=company_id, display_name="Test Co"))
        session.add(
            Document(
                id=document_id,
                company_id=company_id,
                original_file_name="appendix-205.pdf",
                file_path=f"health/{document_id}.pdf",
                domain="health",
                appendix_number=[appendix_number],
                extraction_method="text",
            )
        )

    yield company_id, appendix_number, document_id

    with session_scope() as session:
        # A test may have added a DocumentExtraction for this document (e.g.
        # test_get_appendix_detail_returns_extracted_fields) - its FK would
        # otherwise block deleting the Document below.
        session.query(DocumentExtraction).filter_by(document_id=document_id).delete()
        session.delete(session.get(Document, document_id))
        session.delete(session.get(Company, company_id))


def test_get_appendix_file_returns_404_when_company_unknown() -> None:
    response = client.get("/public/v1/companies/does-not-exist/appendices/205/file")
    assert response.status_code == 404


def test_get_appendix_file_returns_404_when_appendix_number_unknown(
    appendix_document: tuple[str, str, str],
) -> None:
    company_id, _appendix_number, _document_id = appendix_document
    response = client.get(f"/public/v1/companies/{company_id}/appendices/does-not-exist/file")
    assert response.status_code == 404


def test_get_appendix_file_returns_404_when_missing_on_disk(
    appendix_document: tuple[str, str, str],
) -> None:
    company_id, appendix_number, _document_id = appendix_document
    response = client.get(f"/public/v1/companies/{company_id}/appendices/{appendix_number}/file")
    assert response.status_code == 404


def test_get_appendix_file_serves_inline_by_default(
    appendix_document: tuple[str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    company_id, appendix_number, document_id = appendix_document
    file_dir = tmp_path / "raw_documents" / "health"
    file_dir.mkdir(parents=True)
    (file_dir / f"{document_id}.pdf").write_bytes(b"%PDF-1.4 test content")

    response = client.get(f"/public/v1/companies/{company_id}/appendices/{appendix_number}/file")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test content"
    assert response.headers["content-type"] == "application/pdf"
    assert "inline" in response.headers["content-disposition"]


def test_get_appendix_file_download_sets_attachment_disposition(
    appendix_document: tuple[str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    company_id, appendix_number, document_id = appendix_document
    file_dir = tmp_path / "raw_documents" / "health"
    file_dir.mkdir(parents=True)
    (file_dir / f"{document_id}.pdf").write_bytes(b"%PDF-1.4 test content")

    response = client.get(
        f"/public/v1/companies/{company_id}/appendices/{appendix_number}/file",
        params={"download": "true"},
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]


def test_get_appendix_file_handles_appendix_numbers_containing_slashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real appendix numbers can contain a literal "/" (e.g. "09/2023") - the
    route must accept that, not just plain alphanumeric numbers."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    appendix_number = "09/2023"
    document_id = str(uuid.uuid4())
    file_dir = tmp_path / "raw_documents" / "health"
    file_dir.mkdir(parents=True)
    (file_dir / f"{document_id}.pdf").write_bytes(b"%PDF-1.4 slash test")

    try:
        with session_scope() as session:
            session.add(Company(id=company_id, display_name="Test Co"))
            session.add(
                Document(
                    id=document_id,
                    company_id=company_id,
                    original_file_name="appendix-09-2023.pdf",
                    file_path=f"health/{document_id}.pdf",
                    domain="health",
                    appendix_number=[appendix_number],
                    extraction_method="text",
                )
            )

        response = client.get(
            f"/public/v1/companies/{company_id}/appendices/{quote(appendix_number, safe='')}/file"
        )
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 slash test"
    finally:
        with session_scope() as session:
            session.delete(session.get(Document, document_id))
            session.delete(session.get(Company, company_id))


def test_get_appendix_file_serves_most_recent_when_number_repeats_within_company(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    company_id = f"test_company_{uuid.uuid4().hex[:8]}"
    appendix_number = "310"
    older_id, newer_id = str(uuid.uuid4()), str(uuid.uuid4())
    file_dir = tmp_path / "raw_documents" / "health"
    file_dir.mkdir(parents=True)
    (file_dir / f"{older_id}.pdf").write_bytes(b"%PDF-1.4 older")
    (file_dir / f"{newer_id}.pdf").write_bytes(b"%PDF-1.4 newer")

    try:
        with session_scope() as session:
            session.add(Company(id=company_id, display_name="Test Co"))
            session.add(
                Document(
                    id=older_id,
                    company_id=company_id,
                    original_file_name="old.pdf",
                    file_path=f"health/{older_id}.pdf",
                    domain="health",
                    appendix_number=[appendix_number],
                    extraction_method="text",
                )
            )
        with session_scope() as session:
            # Separate insert so created_date orders strictly after the first row.
            session.add(
                Document(
                    id=newer_id,
                    company_id=company_id,
                    original_file_name="new.pdf",
                    file_path=f"health/{newer_id}.pdf",
                    domain="health",
                    appendix_number=[appendix_number],
                    extraction_method="text",
                )
            )

        response = client.get(
            f"/public/v1/companies/{company_id}/appendices/{appendix_number}/file"
        )
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 newer"
    finally:
        with session_scope() as session:
            session.delete(session.get(Document, older_id))
            session.delete(session.get(Document, newer_id))
            session.delete(session.get(Company, company_id))


def test_list_companies_reports_appendix_counts(
    appendix_document: tuple[str, str, str],
) -> None:
    company_id, _appendix_number, _document_id = appendix_document
    response = client.get("/public/v1/companies")
    assert response.status_code == 200
    by_id = {c["id"]: c for c in response.json()}
    assert by_id[company_id]["appendix_count"] == 1


def test_list_appendices_filters_by_domain(
    appendix_document: tuple[str, str, str],
) -> None:
    company_id, appendix_number, _document_id = appendix_document

    health_response = client.get(
        f"/public/v1/companies/{company_id}/appendices", params={"domain": "health"}
    )
    life_response = client.get(
        f"/public/v1/companies/{company_id}/appendices", params={"domain": "life"}
    )
    assert health_response.status_code == 200
    assert life_response.status_code == 200
    assert [a["appendix_number"] for a in health_response.json()] == [[appendix_number]]
    assert life_response.json() == []


def test_get_appendix_detail_returns_404_when_unknown() -> None:
    response = client.get("/public/v1/companies/does-not-exist/appendices/205")
    assert response.status_code == 404


def test_get_appendix_detail_reports_no_extraction_when_none_exists(
    appendix_document: tuple[str, str, str],
) -> None:
    company_id, appendix_number, _document_id = appendix_document
    response = client.get(f"/public/v1/companies/{company_id}/appendices/{appendix_number}")
    assert response.status_code == 200
    body = response.json()
    assert body["has_extraction"] is False
    assert body["coverage_type"] is None
    assert body["insurance_amounts"] == []


def test_get_appendix_detail_returns_extracted_fields(
    appendix_document: tuple[str, str, str],
) -> None:
    company_id, appendix_number, document_id = appendix_document
    with session_scope() as session:
        session.add(
            DocumentExtraction(
                document_id=document_id,
                coverage_type="ביטוח בריאות",
                exclusions=["מחלה קודמת"],
                tables={"tables": [{"title": "t", "headers": ["a"], "rows": [["1"]]}]},
            )
        )

    response = client.get(f"/public/v1/companies/{company_id}/appendices/{appendix_number}")
    assert response.status_code == 200
    body = response.json()
    assert body["has_extraction"] is True
    assert body["coverage_type"] == "ביטוח בריאות"
    assert body["exclusions"] == ["מחלה קודמת"]
    assert body["tables"][0]["headers"] == ["a"]


@pytest.fixture
def matched_appendix_pair() -> Generator[tuple[str, str, str, str, str, str], None, None]:
    """Yields (company_a, appendix_a, company_b, appendix_b, match_id, document_a_id)."""
    company_a = f"test_company_{uuid.uuid4().hex[:8]}"
    company_b = f"test_company_{uuid.uuid4().hex[:8]}"
    appendix_a, appendix_b = "205", "9/2024"
    document_a_id, document_b_id = str(uuid.uuid4()), str(uuid.uuid4())
    match_id = f"{document_a_id}:{document_b_id}"

    with session_scope() as session:
        session.add(Company(id=company_a, display_name="Company A"))
        session.add(Company(id=company_b, display_name="Company B"))
        session.add(
            Document(
                id=document_a_id,
                company_id=company_a,
                original_file_name="a.pdf",
                file_path=f"health/{document_a_id}.pdf",
                domain="health",
                appendix_number=[appendix_a],
                extraction_method="text",
            )
        )
        session.add(
            Document(
                id=document_b_id,
                company_id=company_b,
                original_file_name="b.pdf",
                file_path=f"health/{document_b_id}.pdf",
                domain="health",
                appendix_number=[appendix_b],
                appendix_name="כיסוי מקביל",
                extraction_method="text",
            )
        )
    with session_scope() as session:
        session.add(
            DocumentMatch(
                id=match_id,
                document_id=document_a_id,
                matched_document_id=document_b_id,
                similarity_score=0.97,
                status="auto_confirmed",
            )
        )

    yield company_a, appendix_a, company_b, appendix_b, match_id, document_a_id

    with session_scope() as session:
        session.query(DocumentMatch).filter_by(id=match_id).delete()
        session.delete(session.get(Document, document_a_id))
        session.delete(session.get(Document, document_b_id))
        session.delete(session.get(Company, company_a))
        session.delete(session.get(Company, company_b))


def test_get_appendix_matches_returns_the_cross_company_match(
    matched_appendix_pair: tuple[str, str, str, str, str, str],
) -> None:
    company_a, appendix_a, company_b, appendix_b, _match_id, _document_a_id = matched_appendix_pair

    response = client.get(f"/public/v1/companies/{company_a}/appendices/{appendix_a}/matches")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["company_id"] == company_b
    assert body[0]["appendix_number"] == [appendix_b]
    assert body[0]["appendix_name"] == "כיסוי מקביל"
    assert body[0]["similarity_score"] == 0.97
    assert body[0]["status"] == "auto_confirmed"


def test_get_appendix_matches_is_symmetric_from_either_side(
    matched_appendix_pair: tuple[str, str, str, str, str, str],
) -> None:
    company_a, appendix_a, company_b, appendix_b, _match_id, _document_a_id = matched_appendix_pair

    # appendix_b contains a literal "/" - this also exercises that /matches
    # (a literal-suffix route) isn't shadowed by the greedy detail route.
    response = client.get(
        f"/public/v1/companies/{company_b}/appendices/{quote(appendix_b, safe='')}/matches"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["company_id"] == company_a
    assert body[0]["appendix_number"] == [appendix_a]


def test_get_appendix_matches_filters_by_status(
    matched_appendix_pair: tuple[str, str, str, str, str, str],
) -> None:
    company_a, appendix_a = matched_appendix_pair[0], matched_appendix_pair[1]

    matching_status = client.get(
        f"/public/v1/companies/{company_a}/appendices/{appendix_a}/matches",
        params={"status": "auto_confirmed"},
    )
    other_status = client.get(
        f"/public/v1/companies/{company_a}/appendices/{appendix_a}/matches",
        params={"status": "pending_review"},
    )
    assert len(matching_status.json()) == 1
    assert other_status.json() == []
