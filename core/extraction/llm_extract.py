"""Structured-field extraction via Claude's Message Batches API.

Batches (not synchronous calls) because this is one-time/periodic bulk
processing, not a latency-sensitive path: 50% cheaper, and one call handles
up to 100k documents. Structured Outputs (`output_config.format` with a
JSON Schema) guarantees a parseable response - no ad-hoc "please return
JSON" prompting and no tool-use round-trip needed for a single extraction
per document.
"""

from __future__ import annotations

import time
from typing import Any

from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import ValidationError

from core.extraction.schema import PolicyExtraction
from core.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_TOKENS = 4096

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

    Pydantic's generated schema doesn't set these by default, but Claude's
    structured-output JSON Schema mode needs them on every object (top-level
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
    client: Anthropic, model: str, documents: dict[str, str]
) -> str:
    """Submit one batch request per document; returns the batch id.

    `documents` maps document_id -> cleaned full text.
    """
    schema = _extraction_json_schema()
    requests = [
        Request(
            custom_id=document_id,
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            ),
        )
        for document_id, text in documents.items()
    ]
    batch = client.messages.batches.create(requests=requests)
    logger.info("Submitted extraction batch %s (%d documents)", batch.id, len(requests))
    return batch.id


def wait_for_batch(client: Anthropic, batch_id: str, poll_seconds: float = 30.0) -> None:
    """Block until the batch finishes (Batches API is async, not instant)."""
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            logger.info(
                "Batch %s ended: succeeded=%d errored=%d",
                batch_id,
                batch.request_counts.succeeded,
                batch.request_counts.errored,
            )
            return
        logger.info("Batch %s status=%s, waiting...", batch_id, batch.processing_status)
        time.sleep(poll_seconds)


def collect_extraction_results(
    client: Anthropic, batch_id: str
) -> dict[str, PolicyExtraction | None]:
    """Return document_id -> PolicyExtraction (None if that document failed)."""
    results: dict[str, PolicyExtraction | None] = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            logger.warning("Extraction failed for %s: %s", result.custom_id, result.result.type)
            results[result.custom_id] = None
            continue

        text = next(
            (b.text for b in result.result.message.content if b.type == "text"), None
        )
        if text is None:
            logger.warning("No text block in response for %s", result.custom_id)
            results[result.custom_id] = None
            continue

        try:
            results[result.custom_id] = PolicyExtraction.model_validate_json(text)
        except ValidationError as exc:
            logger.warning("Schema validation failed for %s: %s", result.custom_id, exc)
            results[result.custom_id] = None

    return results
