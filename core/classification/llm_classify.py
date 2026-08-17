"""Taxonomy classification via OpenAI's Batch API.

Deliberately classifies from the document's ALREADY-EXTRACTED structured
fields (PolicyExtraction: coverage_type, coverage_name, eligibility
conditions, disease_list, ...) plus Document identity fields
(appendix_name, department_name) - not from re-reading the raw PDF/OCR
text again. This is a smaller, cheaper input than the ~16k-token full-text
prompt extraction already used, it's the same signal the corpus-level
taxonomy analysis (scripts/analyze_coverage_taxonomy.py) was built from, and
it avoids redundant PDF/OCR work for data that's already sitting in
`document_extractions`.

Structurally identical to core/extraction/llm_extract.py (Batch API +
Structured Outputs, strict JSON Schema) - reused rather than reinvented,
with one difference: the JSON schema's `category_id` (and
`alternative_category_ids` items) are constrained via a JSON Schema `enum`
to the taxonomy's known category_ids, so the model can never invent one.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from core.classification.schema import ClassificationResult
from core.database.models import DocumentExtraction
from core.extraction.batch_polling import call_with_connection_retry, wait_for_batch_resilient
from core.extraction.json_schema_utils import enforce_strict_json_schema, strip_nul_bytes
from core.taxonomy.registry import DEFAULT_VERSION, list_leaf_categories
from core.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_TOKENS = 1000

_SYSTEM_PROMPT_HEADER = """\
אתה מסווג נספחי ביטוח לפי טקסונומיה קבועה. קיבלת את השדות המובנים שכבר \
חולצו מנספח ביטוח אחד (לא את הטקסט המלא). בחר את ה-category_id המתאים \
ביותר מתוך הרשימה הסגורה הבאה בלבד - אסור להמציא category_id שאינו ברשימה. \
אם ההתאמה אינה חד-משמעית, בחר את category_id הטוב ביותר וציין category_id-ים \
נוספים אפשריים ב-alternative_category_ids. אם דבר אינו מתאים לאף קטגוריה \
ספציפית, סווג ל-category_id שמסתיים ב-".other" או ".unclassified" של \
main_category המתאים.

הקטגוריות הזמינות (category_id: תיאור):
"""


def _build_system_prompt(version: str) -> str:
    lines = [_SYSTEM_PROMPT_HEADER]
    for category in list_leaf_categories(version):
        parts = [category.main_category, category.coverage_family]
        if category.coverage_subtype:
            parts.append(category.coverage_subtype)
        path = " / ".join(parts)
        lines.append(f"- {category.category_id} ({path}): {category.display_name_he}")
        if category.description_he:
            lines.append(f"  {category.description_he.strip()}")
    return "\n".join(lines)


def build_classification_input(
    extraction: DocumentExtraction, department_name: str | None, appendix_name: str | None
) -> str:
    """The compact, DB-only text handed to the classifier - mirrors
    PolicyExtraction.embedding_text()'s "structured fields, not raw text"
    philosophy, plus the two identity fields embedding_text() deliberately
    excludes (useful here for classification context, unlike for
    cross-company embedding similarity where they'd bias toward
    appendix-number/name matching instead of content matching)."""
    lines: list[str] = []
    if department_name:
        lines.append(f"department_name: {department_name}")
    if appendix_name:
        lines.append(f"appendix_name: {appendix_name}")
    if extraction.coverage_type:
        lines.append(f"coverage_type: {extraction.coverage_type}")
    if extraction.coverage_name:
        lines.append(f"coverage_name: {extraction.coverage_name}")
    if extraction.eligibility_conditions:
        lines.append(f"eligibility_conditions: {extraction.eligibility_conditions}")
    if extraction.disease_list:
        lines.append(f"disease_list: {', '.join(extraction.disease_list)}")
    if extraction.exclusions:
        lines.append(f"exclusions: {', '.join(extraction.exclusions)}")
    if extraction.restrictions:
        lines.append(f"restrictions: {', '.join(extraction.restrictions)}")
    if extraction.insurance_amounts:
        lines.append(f"insurance_amounts: {', '.join(extraction.insurance_amounts)}")
    if extraction.survival_period:
        lines.append(f"survival_period: {extraction.survival_period}")
    return "\n".join(lines)


def _classification_json_schema(taxonomy_version: str) -> dict[str, Any]:
    schema = enforce_strict_json_schema(ClassificationResult.model_json_schema())
    category_ids = [c.category_id for c in list_leaf_categories(taxonomy_version)]
    schema["properties"]["category_id"]["enum"] = category_ids
    schema["properties"]["alternative_category_ids"]["items"]["enum"] = category_ids
    return schema


def submit_classification_batch(
    client: OpenAI, model: str, documents: dict[str, str], taxonomy_version: str = DEFAULT_VERSION
) -> str:
    """documents maps document_id -> classification input text (see
    build_classification_input)."""
    system_prompt = _build_system_prompt(taxonomy_version)
    schema = _classification_json_schema(taxonomy_version)

    lines = []
    for document_id, text in documents.items():
        body = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "classification_result", "schema": schema, "strict": True},
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

    logger.info("Submitted classification batch %s (%d documents)", batch.id, len(documents))
    return batch.id


# Retries connection errors while polling instead of letting one network
# blip kill a multi-hour run - see core/extraction/batch_polling.py.
wait_for_batch = wait_for_batch_resilient


def collect_classification_results(
    client: OpenAI, batch_id: str, taxonomy_version: str = DEFAULT_VERSION
) -> dict[str, ClassificationResult | None]:
    batch = call_with_connection_retry(
        f"retrieve batch {batch_id}", lambda: client.batches.retrieve(batch_id)
    )
    valid_ids = {c.category_id for c in list_leaf_categories(taxonomy_version)}
    results: dict[str, ClassificationResult | None] = {}

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
                logger.warning("Classification failed for %s: %s", custom_id, entry["error"])
                results[custom_id] = None
                continue

            message_text = entry["response"]["body"]["choices"][0]["message"]["content"]
            try:
                parsed = ClassificationResult.model_validate_json(message_text)
                parsed = ClassificationResult.model_validate(strip_nul_bytes(parsed.model_dump()))
                if parsed.category_id not in valid_ids:
                    logger.warning(
                        "Classification for %s returned unknown category_id=%s; discarding",
                        custom_id,
                        parsed.category_id,
                    )
                    results[custom_id] = None
                    continue
                parsed.alternative_category_ids = [
                    c for c in parsed.alternative_category_ids if c in valid_ids
                ]
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
                    "Classification request-level error for %s: %s", custom_id, entry.get("error")
                )
                results[custom_id] = None

    return results
