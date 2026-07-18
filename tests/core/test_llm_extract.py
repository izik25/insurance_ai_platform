"""Tests run without any real network access: the JSON-schema hardening
helper is pure, and result-collection is exercised against fake objects
shaped like the Batches API response, not a live Anthropic client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.extraction.llm_extract import (
    _extraction_json_schema,
    _strict_schema,
    collect_extraction_results,
)


def test_strict_schema_sets_additional_properties_false_recursively() -> None:
    schema = _extraction_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())

    # PolicyTable is a nested $defs entry (via the `tables` list) - must also be hardened.
    table_def = schema["$defs"]["PolicyTable"]
    assert table_def["additionalProperties"] is False
    assert set(table_def["required"]) == set(table_def["properties"].keys())


def test_strict_schema_is_idempotent() -> None:
    schema = _strict_schema(_extraction_json_schema())
    schema_again = _strict_schema(schema)
    assert schema == schema_again


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock]


@dataclass
class _FakeSucceededResult:
    message: _FakeMessage
    type: str = "succeeded"


@dataclass
class _FakeErroredResult:
    type: str = "errored"


@dataclass
class _FakeBatchEntry:
    custom_id: str
    result: Any


class _FakeBatches:
    def __init__(self, entries: list[_FakeBatchEntry]) -> None:
        self._entries = entries

    def results(self, batch_id: str) -> list[_FakeBatchEntry]:
        return self._entries


class _FakeMessagesResource:
    def __init__(self, entries: list[_FakeBatchEntry]) -> None:
        self.batches = _FakeBatches(entries)


class _FakeClient:
    def __init__(self, entries: list[_FakeBatchEntry]) -> None:
        self.messages = _FakeMessagesResource(entries)


def test_collect_extraction_results_parses_succeeded_entries() -> None:
    valid_json = '{"coverage_type": "ביטוח בריאות"}'
    entries = [
        _FakeBatchEntry("doc-1", _FakeSucceededResult(_FakeMessage([_FakeTextBlock(valid_json)]))),
    ]
    client = _FakeClient(entries)

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-1"] is not None
    assert results["doc-1"].coverage_type == "ביטוח בריאות"


def test_collect_extraction_results_returns_none_for_errored_entries() -> None:
    entries = [_FakeBatchEntry("doc-2", _FakeErroredResult())]
    client = _FakeClient(entries)

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-2"] is None


def test_collect_extraction_results_returns_none_for_invalid_json() -> None:
    entries = [
        _FakeBatchEntry(
            "doc-3", _FakeSucceededResult(_FakeMessage([_FakeTextBlock("not valid json")]))
        ),
    ]
    client = _FakeClient(entries)

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-3"] is None
