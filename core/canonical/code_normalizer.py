"""Canonical-code normalization: maps CanonicalCoverageProfile/DocumentExtraction
free-text phrases onto core/knowledge_base's fixed canonical codes.

Two-pass, per the approved plan (req 6/25): a cheap rule-based pass first
(core/knowledge_base.registry.normalize_to_code - substring match against
canonical names/synonyms, no LLM call at all), then a small LLM-assisted
batch call per document for whatever the rule-based pass couldn't match -
never one LLM call per phrase. A phrase the LLM also can't map to an
existing code is simply left unmapped (no DocumentCanonicalCode row) rather
than inventing a new code outside the dictionary; calibration (Phase 3/5)
is where dictionary gaps get surfaced and the dictionary grows.

v1 populates insured_event/covered_event/exclusion/limitation/
claim_requirement/extension/definition code_categories. `eligibility` isn't
populated as a code set yet - eligibility_normalized is prose, not a clean
list of discrete phrases, and splitting it heuristically (e.g. by comma)
would produce noisy fragments; left for a future taxonomy revision once
real eligibility phrasing patterns have been reviewed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from core.canonical.schema import CanonicalCoverageProfile
from core.database.models import DocumentExtraction
from core.extraction.batch_polling import call_with_connection_retry, wait_for_batch_resilient
from core.extraction.json_schema_utils import enforce_strict_json_schema, strip_nul_bytes
from core.knowledge_base.registry import get_canonical_codes, normalize_to_code
from core.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_TOKENS = 2000

CandidatePhrase = tuple[str, str, str]  # (code_category, phrase, source_field)


class RuleBasedMatch(BaseModel):
    code_category: str
    code: str
    raw_phrase: str
    source_field: str


def extract_candidate_phrases(
    profile: CanonicalCoverageProfile, extraction: DocumentExtraction
) -> list[CandidatePhrase]:
    """Pure code - pulls every discrete phrase worth normalizing out of the
    already-built canonical profile + existing extraction fields."""
    candidates: list[CandidatePhrase] = []
    if profile.insured_event:
        candidates.append(("insured_event", profile.insured_event, "canonical_profile.insured_event"))
    for disease in extraction.disease_list or []:
        candidates.append(("insured_event", disease, "extraction.disease_list"))
    for event in profile.covered_events:
        candidates.append(("covered_event", event, "canonical_profile.covered_events"))
    for exclusion in extraction.exclusions or []:
        candidates.append(("exclusion", exclusion, "extraction.exclusions"))
    for exclusion in profile.exclusions_normalized:
        candidates.append(("exclusion", exclusion, "canonical_profile.exclusions_normalized"))
    for limitation in profile.limitations:
        candidates.append(("limitation", limitation, "canonical_profile.limitations"))
    for requirement in profile.claim_requirements:
        candidates.append(("claim_requirement", requirement, "canonical_profile.claim_requirements"))
    for extension in profile.extensions:
        candidates.append(("extension", extension, "canonical_profile.extensions"))
    for definition in profile.definitions:
        candidates.append(("definition", definition.term, "canonical_profile.definitions"))
    return candidates


def normalize_rule_based(
    candidates: list[CandidatePhrase],
) -> tuple[list[RuleBasedMatch], list[CandidatePhrase]]:
    """Pure code, no LLM. Returns (matched, unmatched)."""
    matched: list[RuleBasedMatch] = []
    unmatched: list[CandidatePhrase] = []
    for code_category, phrase, source_field in candidates:
        code = normalize_to_code(phrase, code_category)
        if code:
            matched.append(
                RuleBasedMatch(
                    code_category=code_category, code=code, raw_phrase=phrase, source_field=source_field
                )
            )
        else:
            unmatched.append((code_category, phrase, source_field))
    return matched, unmatched


class _FallbackMapping(BaseModel):
    index: int
    code: str | None = None  # null if no existing code fits


class _FallbackResponse(BaseModel):
    mappings: list[_FallbackMapping] = Field(default_factory=list)


def _fallback_json_schema(known_codes: list[str]) -> dict[str, Any]:
    schema = enforce_strict_json_schema(_FallbackResponse.model_json_schema())
    mapping_schema = schema["$defs"]["_FallbackMapping"]
    mapping_schema["properties"]["code"]["anyOf"][0]["enum"] = known_codes
    return schema


_FALLBACK_SYSTEM_PROMPT = """\
קיבלת רשימת ביטויים חופשיים מנספח ביטוח, כל אחד עם אינדקס. עבור כל ביטוי, \
בדוק אם הוא תואם באופן ברור לאחד הקודים הקנוניים הבאים (ולא רק דומה \
באופן כללי - נדרשת התאמה עניינית ברורה). אם כן, החזר את הקוד המתאים. אם \
אין קוד מתאים ברשימה, החזר code=null עבור אותו אינדקס. אל תמציא קוד שאינו \
ברשימה.

הקודים הזמינים: {codes}

הביטויים לסיווג:
{phrases}
"""


def submit_fallback_batch(
    client: OpenAI, model: str, documents: dict[str, list[CandidatePhrase]]
) -> str | None:
    """documents maps document_id -> its unmatched candidate phrases (already
    grouped so phrases of different code_categories in the same document
    are still one call, one JSON schema shared across all code_categories
    combined - simplicity over a marginally tighter enum)."""
    all_codes = sorted({c.code for c in get_canonical_codes()})
    if not documents:
        return None

    schema = _fallback_json_schema(all_codes)
    lines = []
    for document_id, candidates in documents.items():
        if not candidates:
            continue
        phrases_text = "\n".join(f"{i}. [{cat}] {phrase}" for i, (cat, phrase, _src) in enumerate(candidates))
        prompt = _FALLBACK_SYSTEM_PROMPT.format(codes=", ".join(all_codes), phrases=phrases_text)
        body = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": "סווג את הביטויים."}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "code_fallback", "schema": schema, "strict": True},
            },
        }
        lines.append(
            json.dumps(
                {"custom_id": document_id, "method": "POST", "url": "/v1/chat/completions", "body": body},
                ensure_ascii=False,
            )
        )

    if not lines:
        return None

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

    logger.info("Submitted canonical-code fallback batch %s (%d documents)", batch.id, len(lines))
    return batch.id


# Retries connection errors while polling instead of letting one network
# blip kill a multi-hour run - see core/extraction/batch_polling.py.
wait_for_batch = wait_for_batch_resilient


def collect_fallback_results(
    client: OpenAI, batch_id: str, documents: dict[str, list[CandidatePhrase]]
) -> dict[str, list[RuleBasedMatch]]:
    """Returns document_id -> list of matches the LLM found among that
    document's unmatched candidates (skips indices it mapped to null)."""
    batch = call_with_connection_retry(
        f"retrieve batch {batch_id}", lambda: client.batches.retrieve(batch_id)
    )
    results: dict[str, list[RuleBasedMatch]] = {}

    if not batch.output_file_id:
        return results

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
            logger.warning("Canonical-code fallback failed for %s: %s", custom_id, entry["error"])
            continue

        message_text = entry["response"]["body"]["choices"][0]["message"]["content"]
        try:
            parsed = _FallbackResponse.model_validate_json(message_text)
        except ValidationError as exc:
            logger.warning("Schema validation failed for %s: %s", custom_id, exc)
            continue

        candidates = documents.get(custom_id, [])
        matches = []
        for mapping in parsed.mappings:
            if mapping.code is None or not (0 <= mapping.index < len(candidates)):
                continue
            code_category, phrase, source_field = candidates[mapping.index]
            matches.append(
                RuleBasedMatch(
                    code_category=code_category, code=mapping.code, raw_phrase=phrase, source_field=source_field
                )
            )
        results[custom_id] = matches

    return results
