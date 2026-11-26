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
from core.database.models import (
    Document,
    DocumentAdditionalFinding,
    DocumentExtraction,
    DocumentQuestionAnswer,
)
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

סדר עדיפות בין המקורות:
- תשובות מאגר השאלות הן המקור המועדף: הן נקראו מהמסמך המלא ומגובות בציטוט.
- השדות המובנים שחולצו קודם הם מקור משני ועלולים להכיל טעויות. כשהם סותרים \
את תשובות מאגר השאלות - תשובות מאגר השאלות קובעות.
- אם מאגר השאלות סימן נושא כ-NOT_FOUND, השאר את השדה המתאים ריק גם אם \
השדות המובנים מכילים ערך.

הגדרות התקופות - סווג לפי המשמעות, לא לפי המילה שהמסמך או מקור קודם השתמשו בה:
- תקופת אכשרה (qualifying_period_text): נספרת מתחילת הביטוח; מקרה ביטוח \
שאירע בתוכה אינו מכוסה כלל.
- תקופת המתנה (waiting_period_text): נספרת מקרות מקרה הביטוח; עד תומה לא \
משולם תגמול (למשל "לא ישולמו תגמולים בעד שלושת החודשים הראשונים").
- תקופת הישרדות (survival_period_text): המבוטח חייב להישאר בחיים מספר ימים \
אחרי מקרה הביטוח כדי לקבל תגמול (למשל "ובלבד שהמבוטח לא נפטר במהלך 14 ימים").
- סעיף על פיצוי במקרה מוות תוך X ימים אינו תקופת הישרדות. הודעה מוקדמת על \
שינוי פרמיה אינה תקופת המתנה.
- לעולם אל תכניס את אותו טקסט ליותר משדה תקופה אחד.
- העתק את הטקסט כפי שמופיע במקור (טקסט חופשי) - אל תמיר לימים בעצמך.

סכומים:
- amounts: רק סכום הביטוח / הפיצוי / תקרת השיפוי של הכיסוי העיקרי, כשהנספח \
עצמו קובע אותו במספר. לא השתתפות עצמית, לא פרמיה/דמי ביטוח, לא מחיר, לא סכום \
לדוגמה, ולא סכום של כיסוי משני או של מקרה מסוים (סרטן מוקדם, ממצא טרום \
סרטני, סוכרת נעורים, התייעצות, טיפול ניסיוני וכד'). כשסכום הביטוח נקבע בדף \
פרטי הביטוח/במפרט - השאר את amounts ריק.
- deductible: השתתפות עצמית שהנספח קובע בפועל בלבד. קיזוז חוב או פרמיה \
מהתגמול, משפט כללי "(אם קיימת)", תשלום חלקי דרך קופת חולים או חלוקה יחסית עם \
ביטוח אחר - אינם השתתפות עצמית (applies=false).
- caps: תקרות לתגמול. כל תקרה כוללת בטקסט שלה על מה היא חלה (למשל "תקרה \
לסרטן מוקדם: 50,000 ₪").

גילאים (age_restrictions):
- min_age/max_age: רק גיל הצטרפות/כניסה לביטוח שהמסמך קובע במפורש. טבלת \
פרמיות לפי גיל אינה מגבלת גיל - במקרה כזה השאר null.
- termination_age: הגיל שבו הכיסוי מסתיים. אל תעתיק אותו ל-max_age.

benefit_type: התחל באחד מהביטויים הבאים, לפי התגמול העיקרי של הכיסוי: \
"פיצוי חד-פעמי", "שיפוי", "פיצוי חודשי", "שירות בעין". אפשר להוסיף אחריו \
הסבר קצר, ותגמול משני רק אחרי העיקרי.

שאר השדות:
- covered_events/covered_conditions: רשימת אירועים/מצבים המכוסים בפועל.
- exclusions_normalized/limitations: הפרד בין חריגים מוחלטים לבין סייגים/מגבלות.
- definitions: הגדרות מהותיות המשנות את משמעות הכיסוי (term + definition).
- additional_findings_summary: סיכום קצר של כל הממצאים הנוספים שנאספו.
"""

# Question-bank answers whose NOT_FOUND status is itself information the
# profile needs (so the model can leave the matching field empty instead of
# filling it from the secondary extracted fields). Every other NOT_FOUND is
# still omitted to keep the input short.
_KEEP_NOT_FOUND = {
    "base.waiting_period_exists",
    "base.waiting_period_details",
    "base.qualifying_period_exists",
    "base.qualifying_period_details",
    "base.survival_period_exists",
    "base.survival_period_details",
    "base.insurance_amount_exists",
    "base.insurance_amount_details",
    "base.deductible_exists",
    "base.age_limitations",
}


def build_profile_input(
    extraction: DocumentExtraction,
    document: Document,
    question_answers: list[DocumentQuestionAnswer],
    additional_findings: list[DocumentAdditionalFinding],
) -> str:
    lines: list[str] = ["# שדות מובנים שחולצו מהמסמך (מקור משני - עלול להכיל טעויות)"]
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

    lines.append("\n# תשובות ממאגר השאלות (מקור מועדף - מגובות בציטוט מהמסמך)")
    for answer in question_answers:
        if answer.status == "NOT_FOUND" and answer.question_id not in _KEEP_NOT_FOUND:
            continue
        text = f"[{answer.status}] {answer.question_id}"
        if answer.answer_text:
            text += f": {answer.answer_text}"
        if answer.evidence_text and answer.status in ("FOUND", "AMBIGUOUS"):
            text += f' (ציטוט: "{answer.evidence_text}")'
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
