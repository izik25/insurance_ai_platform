"""Structured-field extraction via OpenAI's Batch API.

Batches (not synchronous calls) because this is one-time/periodic bulk
processing, not a latency-sensitive path: ~50% cheaper, and one call
handles large volumes. Structured Outputs (`response_format` with a strict
JSON Schema) guarantees a parseable response - no ad-hoc "please return
JSON" prompting needed.

Switched from Anthropic to OpenAI: the user's Anthropic org is blocked by
an account-level identity-verification gate that neither an API key nor
OAuth login could get past (confirmed live, repeatedly). OpenAI is a
separate account, so it sidesteps that specific block. The extraction
schema/prompt/pipeline around this module (embeddings, matching, DB) is
provider-agnostic and unaffected by this swap.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from core.extraction.schema import PolicyExtraction
from core.utils.logging import get_logger

logger = get_logger(__name__)

# Documents with large tables (e.g. a long list of covered surgical
# procedures) can produce a long JSON response - confirmed live, 4096
# truncated one response mid-string. 16000 gives real headroom without
# being unbounded.
_MAX_TOKENS = 16000
_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}

_SYSTEM_PROMPT = """\
אתה מנתח מסמכי ביטוח (נספחי פוליסה) בעברית. קיבלת את הטקסט המלא של נספח \
ביטוח אחד. חלץ ממנו את השדות הבאים, בדיוק כפי שמופיעים במסמך (אל תמציא \
מידע שלא קיים - השאר שדה ריק/null אם המידע לא מופיע):

- coverage_type: סוג הכיסוי (למשל: ביטוח בריאות, ביטוח חיים, מחלות קשות)
- coverage_name: שם הכיסוי/הנספח כפי שמופיע במסמך
- eligibility_conditions: תנאי זכאות לכיסוי
- insurance_amounts: רשימת סכומי ביטוח (עם ההקשר שלהם, למשל "500,000 ש\"ח למקרה מוות")
- qualifying_period: תקופת אכשרה
- waiting_period: תקופת המתנה
- exclusions: רשימת חריגים/מה לא מכוסה
- age_range: טווח גילאים רלוונטי
- restrictions: הגבלות נוספות
- tables: כל טבלה שמופיעה במסמך (כותרת, כותרות עמודות, שורות)
- disease_count: מספר המחלות שיש עליהן כיסוי (אם רלוונטי)
- disease_list: רשימת שמות המחלות המכוסות (אם רלוונטי)
- survival_period: תקופת הישרדות (אם רלוונטי, בעיקר למחלות קשות)

החזר את התשובה בפורמט ה-JSON המבוקש בלבד.
"""


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
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


def _extraction_json_schema() -> dict[str, Any]:
    return _strict_schema(PolicyExtraction.model_json_schema())


def submit_extraction_batch(
    client: OpenAI, model: str, documents: dict[str, str]
) -> str:
    """Submit one batch request per document; returns the batch id.

    `documents` maps document_id -> cleaned full text.
    """
    schema = _extraction_json_schema()
    lines = []
    for document_id, text in documents.items():
        body = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "policy_extraction", "schema": schema, "strict": True},
            },
        }
        request = {
            "custom_id": document_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": body,
        }
        lines.append(json.dumps(request, ensure_ascii=False))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", encoding="utf-8", delete=False
    ) as handle:
        handle.write("\n".join(lines))
        batch_input_path = Path(handle.name)

    try:
        with batch_input_path.open("rb") as upload_handle:
            uploaded = client.files.create(file=upload_handle, purpose="batch")
        batch = client.batches.create(
            input_file_id=uploaded.id, endpoint="/v1/chat/completions", completion_window="24h"
        )
    finally:
        batch_input_path.unlink(missing_ok=True)

    logger.info("Submitted extraction batch %s (%d documents)", batch.id, len(documents))
    return batch.id


def wait_for_batch(client: OpenAI, batch_id: str, poll_seconds: float = 30.0) -> None:
    """Block until the batch finishes (Batches API is async, not instant)."""
    while True:
        batch = client.batches.retrieve(batch_id)
        if batch.status in _TERMINAL_STATUSES:
            logger.info("Batch %s ended with status=%s", batch_id, batch.status)
            return
        logger.info("Batch %s status=%s, waiting...", batch_id, batch.status)
        time.sleep(poll_seconds)


def collect_extraction_results(
    client: OpenAI, batch_id: str
) -> dict[str, PolicyExtraction | None]:
    """Return document_id -> PolicyExtraction (None if that document failed)."""
    batch = client.batches.retrieve(batch_id)
    results: dict[str, PolicyExtraction | None] = {}

    if batch.output_file_id:
        content = client.files.content(batch.output_file_id).text
        for line in content.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry["custom_id"]

            if entry.get("error"):
                logger.warning("Extraction failed for %s: %s", custom_id, entry["error"])
                results[custom_id] = None
                continue

            message_text = entry["response"]["body"]["choices"][0]["message"]["content"]
            try:
                results[custom_id] = PolicyExtraction.model_validate_json(message_text)
            except ValidationError as exc:
                logger.warning("Schema validation failed for %s: %s", custom_id, exc)
                results[custom_id] = None

    if batch.error_file_id:
        error_content = client.files.content(batch.error_file_id).text
        for line in error_content.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry["custom_id"]
            if custom_id not in results:
                logger.warning(
                    "Extraction request-level error for %s: %s", custom_id, entry.get("error")
                )
                results[custom_id] = None

    return results
