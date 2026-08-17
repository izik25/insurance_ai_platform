"""Shared JSON-Schema helper for OpenAI strict Structured Outputs.

core/extraction/llm_extract.py has its own private copy of this (kept as-is
per the taxonomy/matching upgrade's "don't touch working files" rule); every
NEW structured-output caller added by that upgrade (classification,
question-answering, canonical-profile normalization) shares this one
instead of each re-duplicating it.
"""

from __future__ import annotations

from typing import Any


def strip_nul_bytes(value: Any) -> Any:
    """Recursively drop NUL (0x00) characters from strings.

    Same defense as core/extraction/llm_extract.py's private
    _strip_nul_bytes, shared here for the taxonomy/matching upgrade's other
    structured-output callers (classification, question-answering,
    canonical-profile, canonical-code fallback). Source PDFs occasionally
    contain a stray NUL byte (garbled OCR / binary leftover) that the model
    echoes back verbatim in its JSON output; Postgres text/JSONB columns
    reject NUL outright ("A string literal cannot contain NUL (0x00)
    characters") - confirmed live, it crashed a real question-answering run
    partway through (a different script than the one llm_extract.py's
    original fix covered, so this gap wasn't inherited automatically).
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [strip_nul_bytes(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_nul_bytes(item) for key, item in value.items()}
    return value


def enforce_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively enforce `additionalProperties: false` + full `required`.

    Pydantic's generated schema doesn't set these by default, but strict
    JSON Schema structured-output mode needs them on every object (top-level
    and every entry under $defs) to reliably return every key.
    """

    def _visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
            for prop_schema in node["properties"].values():
                _visit(prop_schema)
        for key in ("items", "$defs"):
            value = node.get(key)
            if isinstance(value, dict):
                if key == "$defs":
                    for sub in value.values():
                        _visit(sub)
                else:
                    _visit(value)
        for key in ("anyOf", "allOf", "oneOf"):
            for sub in node.get(key, []):
                _visit(sub)

    _visit(schema)
    return schema
