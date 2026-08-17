"""Pure Python logic on the ORM models - no DB connection needed (unlike
test_database.py, which runs against real PostgreSQL)."""

from __future__ import annotations

from datetime import date, timedelta

from core.database.models import Document


def _document(**overrides: object) -> Document:
    defaults: dict[str, object] = {
        "id": "x",
        "company_id": "harel",
        "original_file_name": "x.pdf",
        "file_path": "health/x.pdf",
        "domain": "health",
        "extraction_method": "manual",
    }
    defaults.update(overrides)
    return Document(**defaults)  # type: ignore[arg-type]


def test_is_active_true_when_no_marketing_dates_at_all() -> None:
    """Every non-Harel company's documents - NULL end date reads as active."""
    assert _document().is_active is True


def test_is_active_true_when_end_date_in_future() -> None:
    doc = _document(marketing_end_date=date.today() + timedelta(days=1))
    assert doc.is_active is True


def test_is_active_true_when_end_date_is_today() -> None:
    doc = _document(marketing_end_date=date.today())
    assert doc.is_active is True


def test_is_active_false_when_end_date_has_passed() -> None:
    doc = _document(marketing_end_date=date.today() - timedelta(days=1))
    assert doc.is_active is False
