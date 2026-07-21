"""Tests run without any real network access: the JSON-schema hardening
helper is pure, and result-collection is exercised against fake objects
shaped like OpenAI's Batch API response, not a live client."""

from __future__ import annotations

import json

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


class _FakeFileContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeBatch:
    def __init__(
        self, status: str, output_file_id: str | None, error_file_id: str | None = None
    ) -> None:
        self.status = status
        self.output_file_id = output_file_id
        self.error_file_id = error_file_id


class _FakeBatches:
    def __init__(self, batch: _FakeBatch) -> None:
        self._batch = batch

    def retrieve(self, batch_id: str) -> _FakeBatch:
        return self._batch


class _FakeFiles:
    def __init__(self, contents: dict[str, str]) -> None:
        self._contents = contents

    def content(self, file_id: str) -> _FakeFileContent:
        return _FakeFileContent(self._contents[file_id])


class _FakeClient:
    def __init__(self, batch: _FakeBatch, contents: dict[str, str]) -> None:
        self.batches = _FakeBatches(batch)
        self.files = _FakeFiles(contents)


def _output_line(custom_id: str, message_content: str) -> str:
    return json.dumps(
        {
            "custom_id": custom_id,
            "response": {"body": {"choices": [{"message": {"content": message_content}}]}},
        },
        ensure_ascii=False,
    )


def test_collect_extraction_results_parses_succeeded_entries() -> None:
    line = _output_line("doc-1", '{"coverage_type": "ביטוח בריאות"}')
    batch = _FakeBatch(status="completed", output_file_id="file-out")
    client = _FakeClient(batch, {"file-out": line})

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-1"] is not None
    assert results["doc-1"].coverage_type == "ביטוח בריאות"


def test_collect_extraction_results_strips_nul_bytes() -> None:
    """Postgres (text columns and JSONB alike) rejects NUL outright - confirmed
    live, it crashed a real extraction run partway through a batch. A stray
    NUL in the source PDF can get echoed back verbatim by the model."""
    message_content = json.dumps(
        {
            "coverage_type": "ביטוח\x00 בריאות",
            "exclusions": ["חריג עם \x00 באמצע"],
            "tables": [{"title": None, "headers": ["a\x00"], "rows": [["1\x00"]]}],
        },
        ensure_ascii=False,
    )
    line = _output_line("doc-nul", message_content)
    batch = _FakeBatch(status="completed", output_file_id="file-out")
    client = _FakeClient(batch, {"file-out": line})

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    extraction = results["doc-nul"]
    assert extraction is not None
    assert "\x00" not in extraction.coverage_type
    assert "\x00" not in extraction.exclusions[0]
    assert "\x00" not in extraction.tables[0].headers[0]
    assert "\x00" not in extraction.tables[0].rows[0][0]


def test_collect_extraction_results_returns_none_for_errored_output_entries() -> None:
    line = json.dumps({"custom_id": "doc-2", "error": {"message": "boom"}})
    batch = _FakeBatch(status="completed", output_file_id="file-out")
    client = _FakeClient(batch, {"file-out": line})

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-2"] is None


def test_collect_extraction_results_returns_none_for_invalid_json() -> None:
    line = _output_line("doc-3", "not valid json")
    batch = _FakeBatch(status="completed", output_file_id="file-out")
    client = _FakeClient(batch, {"file-out": line})

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-3"] is None


def test_collect_extraction_results_reads_error_file_for_request_level_failures() -> None:
    error_line = json.dumps({"custom_id": "doc-4", "error": {"code": "invalid_request"}})
    batch = _FakeBatch(status="completed", output_file_id=None, error_file_id="file-err")
    client = _FakeClient(batch, {"file-err": error_line})

    results = collect_extraction_results(client, "batch-1")  # type: ignore[arg-type]

    assert results["doc-4"] is None
