"""LLM reading-comprehension judge for cross-company document matches.

Lexical corroboration (see `core.matching.lexical`) catches one class of
false positive (two documents sharing only boilerplate) but not another: two
documents can share a genuinely *specific* word while meaning something
different by it. Confirmed live: Hachshara's נספח 1170 (an accelerated-
payment-for-terminal-illness rider, "...עקב מחלה חשוכת מרפא") shared the word
"מרפא" with ~35 of Phoenix's "מרפא זהב/כסף/פלטינה/ארד" critical-illness
products - there "מרפא" is a brand name, not the word "cure" inside the idiom
"חשוכת מרפא" ("incurable"). No closed vocabulary of boilerplate terms can
catch that: the word is genuinely meaningful in both documents, just in
unrelated senses. Only reading both documents' full extracted fields and
judging *meaning* (not token overlap) can.

This module asks an LLM to do exactly that, over every embedding-ranked
candidate pair (see `core.matching.similarity`), using the full
`DocumentExtraction` schema (coverage_type/name, eligibility_conditions,
qualifying/waiting_period, age_range, survival_period, insurance_amounts,
exclusions, restrictions, disease_list) for both sides - not just the three
name-ish fields lexical corroboration looks at. Uses the Batch API (see
`core.extraction.llm_extract`'s docstring for why: cheaper, and this is
periodic bulk processing, not latency-sensitive) so re-judging the whole
corpus (tens of thousands of pairs) stays affordable.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Literal

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from core.utils.logging import get_logger

logger = get_logger(__name__)

# Batch API turned out too slow for interactive use for a corpus this size
# (confirmed live: an 800-pair batch took ~26 minutes to complete, and the
# org's shared 2M-enqueued-token cap for this model forces batches to run
# one at a time - ~98 sequential batches would have taken 40+ hours). Plain
# concurrent chat completions calls (see `judge_pair`/`judge_pairs_concurrent`
# below) trade the Batch API's ~50% price discount for finishing in well
# under an hour - worth it for a one-off backfill like this.
_MAX_RETRIES = 5

# Short: verdict + a 2-sentence reasoning fits well under this (confirmed
# live in the smoke test); keeping it small also shrinks the per-request
# token footprint against the account's TPM cap below.
_MAX_TOKENS = 300

# The account's real limits for gpt-4.1-mini - concurrency alone isn't
# enough to respect these; a request-count gate here would have looked fine
# right up until the shared token budget for the *model*, not this process,
# was exhausted by other in-flight requests. Targets sit at ~85% of the raw
# limits to leave headroom for estimation error (token counts are estimated
# by character count, not the real tokenizer) and any other concurrent usage
# on the account.
#
# NOTE: these were originally 170_000/420, matching the account's tier-1
# limits seen in early 429 error bodies ("Limit 200000" TPM, "Limit 500"
# RPM). The account has since moved to a higher usage tier from accumulated
# spend (confirmed live via response headers: x-ratelimit-limit-tokens:
# 2000000, x-ratelimit-limit-requests: 5000 - a 10x jump) - bumped to match.
# If throughput mysteriously craters again, re-check current limits via
# `client.chat.completions.with_raw_response.create(...).headers` before
# assuming the pacing values below are still right.
_TOKENS_PER_MINUTE = 1_700_000
_REQUESTS_PER_MINUTE = 4_200


class _RateLimiter:
    """Async token-bucket gate for both TPM and RPM, refilled continuously.

    `acquire` blocks until both buckets can afford the request, then debits
    them - so callers naturally serialize behind the account's real
    throughput instead of firing a burst that immediately 429s.
    """

    def __init__(self, tokens_per_minute: int, requests_per_minute: int) -> None:
        self._token_capacity = tokens_per_minute
        self._request_capacity = requests_per_minute
        self._tokens = float(tokens_per_minute)
        self._requests = float(requests_per_minute)
        self._lock = asyncio.Lock()
        self._last_refill = time.monotonic()

    async def acquire(self, estimated_tokens: int) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now
                self._tokens = min(
                    self._token_capacity, self._tokens + elapsed * self._token_capacity / 60
                )
                self._requests = min(
                    self._request_capacity, self._requests + elapsed * self._request_capacity / 60
                )
                if self._tokens >= estimated_tokens and self._requests >= 1:
                    self._tokens -= estimated_tokens
                    self._requests -= 1
                    return
            await asyncio.sleep(0.25)


def _estimate_tokens(doc_a: DocumentJudgeInfo, doc_b: DocumentJudgeInfo) -> int:
    """Rough token estimate (real tokenization needs the model's tokenizer,
    not available here) - used only to pace requests against the account's
    TPM budget, so an approximation biased slightly high is fine."""
    prompt_chars = len(_SYSTEM_PROMPT) + len(build_pair_prompt(doc_a, doc_b))
    return prompt_chars // 3 + _MAX_TOKENS

_SYSTEM_PROMPT = """\
אתה מומחה ביטוח שמשווה בין שני נספחי ביטוח משתי חברות שונות, כדי לקבוע אם \
הם מתארים בפועל את אותו סוג כיסוי ביטוחי - למרות שהניסוח, המספור והשם עשויים \
להיות שונים לגמרי בין החברות.

קיבלת את השדות המובנים שחולצו מכל אחד משני המסמכים. השתמש בהבנת הנקרא של \
המשמעות בפועל, לא בהשוואת מילים:

- מילה זהה יכולה להופיע במשמעויות שונות לגמרי אצל שתי חברות (למשל שם מותג \
מסחרי מול ביטוי לשוני רגיל). אל תסתמך על כך ששתי מילים חופפות - בדוק אם \
המשמעות התוכנית באמת זהה.
- התמקד בשאלה: מהו האירוע הביטוחי המזכה בתשלום, ומהו מנגנון התשלום (סכום \
חד-פעמי / קצבה חודשית / שחרור מפרמיה / הקדמת חלק מסכום ביטוח קיים וכו')? \
שני נספחים עם אותו אירוע ביטוחי ואותו מנגנון תשלום, גם אם מנוסחים אחרת, \
הם אותו כיסוי בפועל.
- verdict="same_coverage" - אותו כיסוי בפועל.
- verdict="different_coverage" - כיסוי שונה במהותו (אירוע ביטוחי שונה, או \
מנגנון תשלום שונה).
- verdict="uncertain" - אין מספיק מידע בשדות שסופקו כדי להחליט בביטחון; \
נדרשת בדיקה אנושית.

החזר גם נימוק קצר (עד 2 משפטים) בעברית.
"""

_JUDGE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reasoning"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["same_coverage", "different_coverage", "uncertain"],
        },
        "reasoning": {"type": "string"},
    },
}


class JudgeVerdict(BaseModel):
    verdict: Literal["same_coverage", "different_coverage", "uncertain"]
    reasoning: str


class DocumentJudgeInfo(BaseModel):
    """The full comparison-relevant field set for one document, one side of a pair."""

    company_id: str
    appendix_name: str | None = None
    coverage_type: str | None = None
    coverage_name: str | None = None
    eligibility_conditions: str | None = None
    qualifying_period: str | None = None
    waiting_period: str | None = None
    age_range: str | None = None
    survival_period: str | None = None
    insurance_amounts: list[str] = []
    exclusions: list[str] = []
    restrictions: list[str] = []
    disease_list: list[str] = []


def _format_side(label: str, info: DocumentJudgeInfo) -> str:
    lines = [f"=== {label} (חברת {info.company_id}) ==="]
    fields = [
        ("שם הנספח", info.appendix_name),
        ("סוג כיסוי", info.coverage_type),
        ("שם כיסוי", info.coverage_name),
        ("תנאי זכאות", info.eligibility_conditions),
        ("תקופת אכשרה", info.qualifying_period),
        ("תקופת המתנה", info.waiting_period),
        ("טווח גילאים", info.age_range),
        ("תקופת הישרדות", info.survival_period),
    ]
    for label_he, value in fields:
        if value:
            lines.append(f"{label_he}: {value}")
    for label_he, values in (
        ("סכומי ביטוח", info.insurance_amounts),
        ("חריגים", info.exclusions),
        ("הגבלות", info.restrictions),
        ("רשימת מחלות", info.disease_list),
    ):
        if values:
            lines.append(f"{label_he}: " + "; ".join(values))
    return "\n".join(lines)


def build_pair_prompt(doc_a: DocumentJudgeInfo, doc_b: DocumentJudgeInfo) -> str:
    return _format_side("מסמך A", doc_a) + "\n\n" + _format_side("מסמך B", doc_b)


def submit_judge_batch(
    client: OpenAI, model: str, pairs: dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]]
) -> str:
    """Submit one batch request per candidate pair; returns the batch id.

    `pairs` maps a pair_id (caller's choice, e.g. "doc_a_id|||doc_b_id") to
    the two documents' judge-relevant fields.
    """
    lines = []
    for pair_id, (doc_a, doc_b) in pairs.items():
        body = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": build_pair_prompt(doc_a, doc_b)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "judge_verdict", "schema": _JUDGE_JSON_SCHEMA, "strict": True},
            },
        }
        request = {
            "custom_id": pair_id,
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

    logger.info("Submitted judge batch %s (%d pairs)", batch.id, len(pairs))
    return batch.id


def collect_judge_results(client: OpenAI, batch_id: str) -> dict[str, JudgeVerdict | None]:
    """Return pair_id -> JudgeVerdict (None if that pair failed)."""
    batch = client.batches.retrieve(batch_id)
    results: dict[str, JudgeVerdict | None] = {}

    if batch.output_file_id:
        content = client.files.content(batch.output_file_id).text
        for line in content.splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry["custom_id"]

            if entry.get("error"):
                logger.warning("Judge failed for %s: %s", custom_id, entry["error"])
                results[custom_id] = None
                continue

            message_text = entry["response"]["body"]["choices"][0]["message"]["content"]
            try:
                results[custom_id] = JudgeVerdict.model_validate_json(message_text)
            except ValidationError as exc:
                logger.warning("Judge schema validation failed for %s: %s", custom_id, exc)
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
                    "Judge request-level error for %s: %s", custom_id, entry.get("error")
                )
                results[custom_id] = None

    return results


# Substrings confirmed live in real 429 error bodies for a drained/expired
# OpenAI balance ("insufficient_quota"/"credit_balance_exhausted") - unlike
# an ordinary rate limit, this never clears on its own, so retrying (let
# alone letting *every other concurrent worker* keep retrying too) only
# burns wall-clock time. Confirmed live: with concurrency=150 and no fast
# fail, a drained balance mid-run kept ~150 workers retrying for over 20
# minutes (surfacing partly as RateLimitError, partly as APIConnectionError
# once the sustained retry storm itself started getting connections
# dropped) before the run gave up - `abort_event` cuts that to seconds.
_QUOTA_EXHAUSTED_MARKERS = ("insufficient_quota", "credit_balance_exhausted")


def _is_quota_exhausted(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _QUOTA_EXHAUSTED_MARKERS)


async def judge_pair(
    client: AsyncOpenAI,
    model: str,
    doc_a: DocumentJudgeInfo,
    doc_b: DocumentJudgeInfo,
    rate_limiter: _RateLimiter,
    abort_event: asyncio.Event,
) -> JudgeVerdict | None:
    """Judge one pair via a plain (non-batch) chat completion, with retries.

    Retries transient failures (rate limits, connection errors, 5xx) with
    exponential backoff - expected occasionally even with `rate_limiter`
    pacing requests (the token estimate is approximate, and other usage on
    the account isn't visible to it). Returns None only after exhausting
    retries or on a genuine schema-validation failure.

    Checks `abort_event` before spending a rate-limiter slot or making a
    request at all - once *any* call detects a drained account balance (see
    `_is_quota_exhausted`), it sets the event so every other in-flight/
    queued worker bails out immediately instead of independently
    rediscovering the same dead end.
    """
    if abort_event.is_set():
        return None

    estimated_tokens = _estimate_tokens(doc_a, doc_b)
    delay = 2.0
    for attempt in range(_MAX_RETRIES):
        await rate_limiter.acquire(estimated_tokens)
        try:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": build_pair_prompt(doc_a, doc_b)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "judge_verdict",
                        "schema": _JUDGE_JSON_SCHEMA,
                        "strict": True,
                    },
                },
            )
            message_text = response.choices[0].message.content
            return JudgeVerdict.model_validate_json(message_text)
        except ValidationError as exc:
            logger.warning("Judge schema validation failed: %s", exc)
            return None
        except (RateLimitError, APIConnectionError, APIStatusError) as exc:
            if _is_quota_exhausted(exc):
                if not abort_event.is_set():
                    logger.warning("Account balance exhausted - aborting remaining pairs: %s", exc)
                abort_event.set()
                return None
            if attempt == _MAX_RETRIES - 1:
                logger.warning("Judge call failed after %d attempts: %s", _MAX_RETRIES, exc)
                return None
            await asyncio.sleep(delay)
            delay *= 2
    return None


async def judge_pairs_concurrent(
    model: str,
    pairs: dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]],
    concurrency: int,
    on_result: "callable[[str, JudgeVerdict | None], None]",
    api_key: str | None = None,
) -> None:
    """Judge every pair concurrently (bounded by `concurrency` *and* by a
    shared `_RateLimiter` tracking the account's real TPM/RPM budget),
    calling `on_result(pair_id, verdict)` as each one finishes - lets the
    caller checkpoint incrementally instead of holding everything in memory
    until the very end (so a crash partway through doesn't lose completed
    work).

    `concurrency` alone isn't a safe throttle (confirmed live: concurrency=40
    with no rate awareness exhausted the account's real 200k-TPM/500-RPM
    budget for gpt-4.1-mini within the first second and every request failed
    even after 5 retries, since new requests kept arriving faster than the
    budget refilled) - `_RateLimiter` is what actually keeps this under the
    account's throughput; `concurrency` here just bounds how many requests
    can be in flight waiting on it at once.

    `api_key` is passed explicitly (rather than relying on AsyncOpenAI's
    default OPENAI_API_KEY env-var lookup) because this project reads
    secrets from .env via pydantic-settings, which populates `Settings`,
    not the process environment.
    """
    client = AsyncOpenAI(api_key=api_key)
    semaphore = asyncio.Semaphore(concurrency)
    rate_limiter = _RateLimiter(_TOKENS_PER_MINUTE, _REQUESTS_PER_MINUTE)
    abort_event = asyncio.Event()

    async def _worker(pair_id: str, doc_a: DocumentJudgeInfo, doc_b: DocumentJudgeInfo) -> None:
        async with semaphore:
            verdict = await judge_pair(client, model, doc_a, doc_b, rate_limiter, abort_event)
        on_result(pair_id, verdict)

    await asyncio.gather(*(_worker(pid, a, b) for pid, (a, b) in pairs.items()))
    await client.close()
    if abort_event.is_set():
        logger.warning(
            "Run aborted early due to exhausted account balance - re-run once credit is added "
            "to pick up where this left off (unjudged pairs weren't checkpointed as failures)."
        )
