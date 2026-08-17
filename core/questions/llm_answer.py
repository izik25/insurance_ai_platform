"""Question Bank answering via OpenAI's Batch API.

Structurally identical to core/extraction/llm_extract.py (Batch API +
Structured Outputs, strict JSON Schema), but - unlike extraction and
classification - each document in a batch can have a genuinely different
JSON schema and system prompt, because each document's applicable question
set (core/knowledge_base.get_questions_for_category) depends on which
taxonomy category it was classified into. The Batch API supports this
fine: each JSONL line is an independent request body.

Answers a document's ENTIRE applicable question set (base + category-
specific) in a single call, not one call per question - this is the single
most important cost-control decision in the taxonomy/matching upgrade (see
the approved plan's "risks" section): ~20-30 structured answers per
document costs roughly the same shape as the existing extraction backfill
(one call per document), not 20-30x it.

Unlike extraction/classification, this DOES need the full raw document
text (not just already-extracted fields) - evidence_text/evidence_page
require reading the actual document.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from core.extraction.batch_polling import call_with_connection_retry, wait_for_batch_resilient
from core.extraction.json_schema_utils import enforce_strict_json_schema, strip_nul_bytes
from core.knowledge_base.schema import QuestionDef
from core.questions.schema import ANSWER_STATUSES, QuestionAnswerBatch, QuestionAnswerItem
from core.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_TOKENS = 8000

_SYSTEM_PROMPT_HEADER = """\
אתה עונה על שאלות לגבי נספח ביטוח בעברית, על סמך הטקסט המלא של המסמך \
שתקבל. עבור כל שאלה ברשימה למטה, החזר תשובה במבנה הבא:

- status: אחד מהערכים הבאים בלבד:
  * FOUND - המסמך עונה במפורש על השאלה.
  * NOT_FOUND - המסמך אינו מתייחס לנושא בכלל (לא הסק ש"לא קיים" - רק ש"לא נמצא").
  * NOT_APPLICABLE - השאלה אינה רלוונטית לסוג הכיסוי הזה מיסודו.
  * AMBIGUOUS - יש התייחסות אך אינה חד-משמעית, או שאינך בטוח.
- answer_text: התשובה בפועל (null אם status אינו FOUND/AMBIGUOUS עם תוכן חלקי).
- evidence_text: ציטוט קצר מהמסמך התומך בתשובה (null אם אין).
- evidence_page: מספר העמוד שבו נמצא הציטוט, אם ידוע (null אם לא).
- evidence_section: כותרת הסעיף/הפרק הרלוונטי, אם ידוע (null אם לא).

החזר תשובה לכל אחת מהשאלות הבאות (question_id: טקסט השאלה):
"""


def _build_system_prompt(questions: list[QuestionDef]) -> str:
    lines = [_SYSTEM_PROMPT_HEADER]
    for question in questions:
        lines.append(f"- {question.question_id}: {question.text_he}")
    lines.append(
        "\nבנוסף, ציין ב-additional_findings כל מידע מהותי מהמסמך שאינו נענה "
        "באף אחת מהשאלות למעלה ואינו נכלל בשדות הסכמה הקיימת."
    )
    return "\n".join(lines)


def _answer_json_schema(question_ids: list[str]) -> dict[str, Any]:
    schema = enforce_strict_json_schema(QuestionAnswerBatch.model_json_schema())
    item_schema = schema["$defs"]["QuestionAnswerItem"]
    item_schema["properties"]["question_id"]["enum"] = question_ids
    item_schema["properties"]["status"]["enum"] = sorted(ANSWER_STATUSES)
    return schema


def submit_question_batch(
    client: OpenAI, model: str, documents: dict[str, tuple[str, list[QuestionDef]]]
) -> str:
    """documents maps document_id -> (full_document_text, applicable_questions)."""
    lines = []
    for document_id, (text, questions) in documents.items():
        question_ids = [q.question_id for q in questions]
        body = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "messages": [
                {"role": "system", "content": _build_system_prompt(questions)},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "question_answer_batch",
                    "schema": _answer_json_schema(question_ids),
                    "strict": True,
                },
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

    logger.info("Submitted question-bank batch %s (%d documents)", batch.id, len(documents))
    return batch.id


# Retries connection errors while polling instead of letting one network
# blip kill a multi-hour run - see core/extraction/batch_polling.py.
wait_for_batch = wait_for_batch_resilient


def _dedupe_answers(batch: QuestionAnswerBatch, document_id: str) -> QuestionAnswerBatch:
    """Rule-based safety net: nothing in the JSON Schema stops the model
    from returning the same question_id twice in one response (arrays have
    no uniqueness constraint), and it does happen in practice - confirmed
    live, it violates document_question_answers' (document_id,
    question_bank_version, question_id) unique index. Keep the first
    occurrence (arbitrary but deterministic), drop the rest, log so it's
    visible how often this happens."""
    seen: set[str] = set()
    deduped: list[QuestionAnswerItem] = []
    for answer in batch.answers:
        if answer.question_id in seen:
            logger.warning(
                "Document %s: model returned duplicate answer for %s; dropping the repeat",
                document_id,
                answer.question_id,
            )
            continue
        seen.add(answer.question_id)
        deduped.append(answer)
    batch.answers = deduped
    return batch


def _fill_missing_answers(
    batch: QuestionAnswerBatch, expected_question_ids: list[str], document_id: str
) -> QuestionAnswerBatch:
    """Rule-based safety net (not an LLM decision): if the model's response
    omits a question that was asked, that's a pipeline gap, not a content
    signal - synthesize an AMBIGUOUS placeholder rather than silently
    dropping it or letting it read as NOT_FOUND (which would incorrectly
    imply the document was checked and found silent)."""
    present = {a.question_id for a in batch.answers}
    missing = [qid for qid in expected_question_ids if qid not in present]
    if missing:
        logger.warning(
            "Document %s: model omitted %d/%d requested answers: %s",
            document_id,
            len(missing),
            len(expected_question_ids),
            missing,
        )
    for question_id in missing:
        batch.answers.append(
            QuestionAnswerItem(
                question_id=question_id,
                status="AMBIGUOUS",
                answer_text=None,
                evidence_text="[pipeline: model did not return an answer for this question]",
            )
        )
    return batch


def collect_question_results(
    client: OpenAI, batch_id: str, expected_question_ids: dict[str, list[str]]
) -> dict[str, QuestionAnswerBatch | None]:
    """expected_question_ids maps document_id -> the question_ids that were
    asked for that document, used for the missing-answer safety net."""
    batch = call_with_connection_retry(
        f"retrieve batch {batch_id}", lambda: client.batches.retrieve(batch_id)
    )
    results: dict[str, QuestionAnswerBatch | None] = {}

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
                logger.warning("Question answering failed for %s: %s", custom_id, entry["error"])
                results[custom_id] = None
                continue

            message_text = entry["response"]["body"]["choices"][0]["message"]["content"]
            try:
                parsed = QuestionAnswerBatch.model_validate_json(message_text)
                parsed = QuestionAnswerBatch.model_validate(strip_nul_bytes(parsed.model_dump()))
                parsed = _dedupe_answers(parsed, custom_id)
                parsed = _fill_missing_answers(
                    parsed, expected_question_ids.get(custom_id, []), custom_id
                )
                results[custom_id] = parsed
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
                    "Question-bank request-level error for %s: %s", custom_id, entry.get("error")
                )
                results[custom_id] = None

    return results
