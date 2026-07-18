"""Tests for `PolicyExtraction` — the uniform structured-field schema."""

from __future__ import annotations

from core.extraction.schema import PolicyExtraction, PolicyTable


def test_all_fields_optional_with_sensible_defaults() -> None:
    extraction = PolicyExtraction()
    assert extraction.coverage_type is None
    assert extraction.insurance_amounts == []
    assert extraction.tables == []


def test_embedding_text_combines_scalar_and_list_fields() -> None:
    extraction = PolicyExtraction(
        coverage_type="ביטוח בריאות",
        coverage_name="ניתוחים",
        exclusions=["מחלה קודמת"],
        disease_list=["סרטן", "שבץ"],
    )
    text = extraction.embedding_text()
    assert "ביטוח בריאות" in text
    assert "ניתוחים" in text
    assert "מחלה קודמת" in text
    assert "סרטן" in text
    assert "שבץ" in text


def test_embedding_text_skips_none_and_empty_fields() -> None:
    extraction = PolicyExtraction(coverage_type="ביטוח חיים")
    text = extraction.embedding_text()
    assert text == "ביטוח חיים"


def test_embedding_text_empty_when_nothing_extracted() -> None:
    assert PolicyExtraction().embedding_text() == ""


def test_tables_round_trip() -> None:
    table = PolicyTable(title="סכומי ביטוח", headers=["גיל", "סכום"], rows=[["0-18", "100000"]])
    extraction = PolicyExtraction(tables=[table])
    assert extraction.tables[0].headers == ["גיל", "סכום"]
    assert extraction.tables[0].rows == [["0-18", "100000"]]
