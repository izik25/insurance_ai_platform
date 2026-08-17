"""Canonical Coverage Profile normalization via OpenAI's Batch API.

Input is deliberately DB-only (DocumentExtraction fields + this document's
own DocumentQuestionAnswer/DocumentAdditionalFinding rows), not a re-read of
the raw PDF - by this stage everything the profile needs has already been
extracted/answered once; normalizing it into one company-phrasing-
independent structure is a pure "reorganize what we already know" step, so
it stays cheap and doesn't re-touch OCR/PDF reading at all.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from core.canonical.schema import CanonicalCoverageProfile
from core.database.models import Document, DocumentAdditionalFinding, DocumentExtraction, DocumentQuestionAnswer
from core.extraction.batch_polling import call_with_connection_retry, wait_for_batch_resilient
from core.extraction.json_schema_utils import enforce_strict_json_schema, strip_nul_bytes
from core.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_TOKENS = 4000

_SYSTEM_PROMPT = """\
אתה מנרמל מידע שכבר נאסף על נספח ביטוח לפרופיל כיסוי אחיד (Canonical \
Coverage Profile), שמטרתו לתאר את משמעות הכיסוי בצורה עקבית שלא תלויה \
בניסוח הספציפי של חברת הביטוח. קיבלת את השדות המובנים שכבר חולצו מהמסמך \
ואת התשובות שכבר נאספו על מאגר השאלות. אל תמציא מידע חדש שלא מופיע במקורות \
שקיבלת - התפקיד שלך הוא לארגן ולנסח מחדש, לא לחלץ מידע נוסף. אם מידע חסר, \
השאר את השדה הרלוונטי ריק/null.

הקפד:
- covered_events/covered_conditions: רשימת אירועים/מצבים המכוסים בפועל.
- exclusions_normalized/limitations: הפרד בין חריגים מוחלטים לבין סייגים/מגבלות.
- waiting_period_text/qualifying_period_text/survival_period_text: כפי \
שמופיע במקור (טקסט חופשי) - אל תמיר לימים בעצמך.
- deductible/age_restrictions: מבנה קבוע כפי שהוגדר בסכמה.
- definitions: הגדרות מהותיות המשנות את משמעות הכיסוי (term + definition).
- additional_findings_summary: סיכום קצר של כל הממצאים הנוספים שנאספו.
"""


def build_profile_input(
    extraction: DocumentExtraction,
    document: Document,
    question_answers: list[DocumentQuestionAnswer],
    additional_findings: list[DocumentAdditionalFinding],
) -> str:
    lines: list[str] = ["# שדות מובנים שחולצו מהמסמך"]
    for field_name in (
        "coverage_type",
        "coverage_name",
        "eligibility_conditions",
        "qualifying_period",
        "waiting_period",
        "survival_period",
        "age_range",
    ):
        value = getattr(extraction, field_name)
        if value:
            lines.append(f"{field_name}: {value}")
    for field_name in ("insurance_amounts", "exclusions", "restrictions", "disease_list"):
        value = getattr(extraction, field_name)
        if value:
            lines.append(f"{field_name}: {', '.join(value)}")
    if document.department_name:
        lines.append(f"department_name: {document.department_name}")

    lines.append("\n# תשובות ממאגר השאלות")
    for answer in question_answers:
        if answer.status == "NOT_FOUND":
            continue
        text = f"[{answer.status}] {answer.question_id}"
        if answer.answer_text:
            text += f": {answer.answer_text}"
        lines.append(text)

    if additional_findings:
        lines.append("\n# ממצאים נוספים")
        for finding in additional_findings:
            lines.append(f"- {finding.finding_text}")

    return "\n".join(lines)


def _profile_json_schema() -> dict[str, Any]:
    return enforce_strict_json_schema(CanonicalCoverageProfile.model_json_schema())


def submit_profile_batch(client: OpenAI, model: str, documents: dict[str, str]) -> str:
    schema = _profile_json_schema()
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
                "json_schema": {"name": "canonical_coverage_profile", "schema": schema, "strict": True},
            },
        }
        lines.append(
            json.dumps(
                {"custom_id": document_id, "method": "POST", "url": "/v1/chat/completions", "body": body},
                ensure_ascii=False,
            )
        )

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

    logger.info("Submitted canonical-profile batch %s (%d documents)", batch.id, len(documents))
    return batch.id


# Retries connection errors while polling instead of letting one network
# blip kill a multi-hour run - see core/extraction/batch_polling.py.
wait_for_batch = wait_for_batch_resilient


def collect_profile_results(client: OpenAI, batch_id: str) -> dict[str, CanonicalCoverageProfile | None]:
    batch = call_with_connection_retry(
        f"retrieve batch {batch_id}", lambda: client.batches.retrieve(batch_id)
    )
    results: dict[str, CanonicalCoverageProfile | None] = {}

    if batch.output_file_id:
        content = call_with_connection_retry(
            f"fetch output file for batch {batch_id}",
            lambda: client.files.content(batch.output_file_id).text,
        )
        for line in content.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry["custom_id"]

            if entry.get("error"):
                logger.warning("Canonical profile failed for %s: %s", custom_id, entry["error"])
                results[custom_id] = None
                continue

            message_text = entry["response"]["body"]["choices"][0]["message"]["content"]
            try:
                parsed = CanonicalCoverageProfile.model_validate_json(message_text)
                results[custom_id] = CanonicalCoverageProfile.model_validate(
                    strip_nul_bytes(parsed.model_dump())
                )
            except ValidationError as exc:
                logger.warning("Schema validation failed for %s: %s", custom_id, exc)
                results[custom_id] = None

    if batch.error_file_id:
        error_content = call_with_connection_retry(
            f"fetch error file for batch {batch_id}",
            lambda: client.files.content(batch.error_file_id).text,
        )
        for line in error_content.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry["custom_id"]
            if custom_id not in results:
                logger.warning(
                    "Canonical-profile request-level error for %s: %s", custom_id, entry.get("error")
                )
                results[custom_id] = None

    return results
