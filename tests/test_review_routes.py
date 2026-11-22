"""Client review page endpoints (/api/review). Runs against the real local
PostgreSQL instance like test_api_routes.py; each test cleans up its own rows."""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from core.database.models import ReviewVerdict
from core.database.session import init_db, session_scope
from main import app

client = TestClient(app)
CODE = "test-review-code"


@pytest.fixture(autouse=True, scope="module")
def _ensure_schema() -> None:
    init_db()


@pytest.fixture
def review_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEW_ACCESS_CODE", CODE)


@pytest.fixture
def key() -> Generator[str, None, None]:
    k = f"c-test-{uuid.uuid4().hex[:10]}"
    yield k
    with session_scope() as session:
        session.query(ReviewVerdict).filter(ReviewVerdict.key == k).delete()


def test_disabled_without_configured_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEW_ACCESS_CODE", "")
    assert client.get("/api/review/verdicts", headers={"X-Review-Code": CODE}).status_code == 503


@pytest.mark.usefixtures("review_enabled")
def test_rejects_missing_or_wrong_code() -> None:
    assert client.get("/api/review/verdicts").status_code == 401
    assert client.get("/api/review/verdicts", headers={"X-Review-Code": "nope"}).status_code == 401
    assert client.get("/api/review/check", headers={"X-Review-Code": CODE}).status_code == 204


@pytest.mark.usefixtures("review_enabled")
def test_save_then_list_and_overwrite(key: str) -> None:
    headers = {"X-Review-Code": CODE}
    first = {"verdict": "wrong", "note": "הסכום לא תואם", "company": "clal"}
    assert client.put(f"/api/review/verdicts/{key}", json=first, headers=headers).status_code == 200
    assert client.get("/api/review/verdicts", headers=headers).json()[key] == first

    second = {**first, "verdict": "partial"}
    response = client.put(f"/api/review/verdicts/{key}", json=second, headers=headers)
    assert response.status_code == 200
    assert client.get("/api/review/verdicts", headers=headers).json()[key]["verdict"] == "partial"


@pytest.mark.usefixtures("review_enabled")
def test_rejects_bad_key_and_oversized_entry(key: str) -> None:
    headers = {"X-Review-Code": CODE}
    assert client.put("/api/review/verdicts/x y", json={}, headers=headers).status_code == 422
    big = {"note": "א" * 20_000}
    assert client.put(f"/api/review/verdicts/{key}", json=big, headers=headers).status_code == 413
