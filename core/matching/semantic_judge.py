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
import re
import tempfile
import time
from pathlib import Path
from typing import Literal

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError, model_validator

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
_MAX_TOKENS = 1200

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

קיבלת מכל אחד משני המסמכים את המידע שחולץ ממנו. כשמופיעות "תשובות לבנק \
השאלות" - אלה תשובות לאותן שאלות בדיוק, שנשאלו על שני המסמכים מתוך הטקסט \
המלא שלהם; השווה אותן שאלה מול שאלה. השתמש בהבנת הנקרא של המשמעות בפועל, \
לא בהשוואת מילים:

- מילה זהה יכולה להופיע במשמעויות שונות לגמרי אצל שתי חברות (למשל שם מותג \
מסחרי מול ביטוי לשוני רגיל). אל תסתמך על כך ששתי מילים חופפות - בדוק אם \
המשמעות התוכנית באמת זהה.
- "לא נמצא" במסמך אחד אינו אומר שהכיסוי חסר בו - ייתכן שפשוט לא נכתב. אל \
תסיק הבדל מהותי רק מתשובה חסרה.

ענה על שתי שאלות בלבד - ההכרעה הסופית מחושבת מהן:

1. core_insured_event - האירוע הביטוחי העיקרי, זה שמזכה בתגמול העיקרי של \
הנספח (למשל "גילוי מחלת סרטן לראשונה"). התעלם מכיסויים משניים ומהרחבות \
(הישנות המחלה, סרטן מוקדם בתשלום חלקי, חוות דעת שניה, ליווי, שיפוי נוסף):
   * "same" - אותו אירוע עיקרי באותו היקף (גם אם הניסוח שונה).
   * "narrower_in_a" / "narrower_in_b" - האירוע העיקרי באותו נספח מוגבל רק \
לחלק מהמקרים שהשני מכסה (למשל רק סרטן השד והרחם מול כל סוגי הסרטן, או רק \
ילדים מול כלל המבוטחים). היעדר כיסוי משני בנספח אחד (הישנות המחלה, מקרה \
שני, סרטן מוקדם, הרחבות) אינו הופך את האירוע העיקרי שלו לצר יותר - זה הבדל \
שנרשם רק ב-material_differences.
   * "different" - אירוע עיקרי אחר.
   * "unclear" - אי אפשר לקבוע מהמידע.
2. core_payment_mechanism - מנגנון התגמול העיקרי (סכום חד-פעמי / תשלומים \
חודשיים / שיפוי הוצאות / שירות / שחרור מפרמיה):
   * "same", "different" או "unclear". נספח שמשלם סכום חד-פעמי וגם נותן \
הרחבות שיפוי או שירות - מנגנונו העיקרי הוא עדיין סכום חד-פעמי.

הבדלים בתקופות (אכשרה/המתנה/הישרדות), בסכומים ובאחוזים, בגילאי כניסה וסיום, \
בחריגים ובכיסויים המשניים וההרחבות אינם משפיעים על שתי השאלות - אבל חובה לפרט \
כל אחד מהם ב-material_differences.

החזר גם נימוק קצר (עד 2 משפטים) בעברית, ו-material_differences: רשימת \
ההבדלים המהותיים בין שני המסמכים (נושא, מה כתוב ב-A, מה כתוב ב-B), בעברית. \
רשימה ריקה אם אין הבדלים.

כללים ל-material_differences:
- כל צד חייב להיות קביעה ברורה: מה הנספח קובע (עם המספר אם יש), או "לא מכוסה \
בנספח" כשהנספח מפרט רשימה סגורה של מה שמכוסה והנושא לא בה, או "לא מוזכר \
בנספח". אסור לנסח בערפול כמו "לא צוין במפורש ככיסוי עיקרי" או "ייתכנו הבדלים".
- רשום הבדל רק כשיש לפחות צד אחד עם מידע ממשי. אם בשני הצדדים אין מידע - זה \
אינו הבדל.
- סכום ביטוח: תקרה של מקרה מסוים או של כיסוי משני (סרטן מוקדם, סוכרת נעורים, \
התייעצות, טיפול ניסיוני, אחוז מסכום הביטוח) אינה סכום הביטוח - כתוב אותה \
כהבדל בנושא של אותו מקרה, לא כ"סכום ביטוח מקסימלי". כשבשני הנספחים סכום \
הביטוח נקבע בדף פרטי הביטוח - אין כאן הבדל.
- השתתפות עצמית: רק סכום/אחוז שהמבוטח נושא בכל תביעה. זכות החברה לקזז חוב או \
פרמיה מהתגמול, או משפט כללי "(אם קיימת)", אינם השתתפות עצמית.
"""

_JUDGE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["core_insured_event", "core_payment_mechanism", "reasoning", "material_differences"],
    "properties": {
        "core_insured_event": {
            "type": "string",
            "enum": ["same", "narrower_in_a", "narrower_in_b", "different", "unclear"],
        },
        "core_payment_mechanism": {"type": "string", "enum": ["same", "different", "unclear"]},
        "reasoning": {"type": "string"},
        "material_differences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic", "document_a", "document_b"],
                "properties": {
                    "topic": {"type": "string"},
                    "document_a": {"type": "string"},
                    "document_b": {"type": "string"},
                },
            },
        },
    },
}


# "Similar, but there may be subtle differences" is not a difference - the
# prompt forbids it, the model still wrote it on ~8% of pairs (2026-11 review
# round), so such entries are dropped in code.
_VAGUE_DIFFERENCE = re.compile(
    r"ייתכנו הבדלים|ייתכן שקיימים הבדלים|ייתכן שיש הבדלים|הבדלים דקים|הבדלים קלים|ייתכנו שינויים"
)


def is_vague_difference(document_a: str, document_b: str) -> bool:
    return bool(_VAGUE_DIFFERENCE.search(document_a or "") or _VAGUE_DIFFERENCE.search(document_b or ""))


class MaterialDifference(BaseModel):
    topic: str
    document_a: str
    document_b: str


class JudgeVerdict(BaseModel):
    """The verdict is computed in code from the two core axes, never taken
    from the model directly: asked for an overall verdict, the model (given
    full question-bank answers) called 33 of 40 cancer pilot pairs
    "different" over coverage-end ages, qualifying periods and secondary
    benefits - exactly the differences the prompt said not to decide on."""

    core_insured_event: Literal["same", "narrower_in_a", "narrower_in_b", "different", "unclear"] = (
        "unclear"
    )
    core_payment_mechanism: Literal["same", "different", "unclear"] = "unclear"
    verdict: Literal["same_coverage", "different_coverage", "uncertain"] = "uncertain"
    reasoning: str
    material_differences: list[MaterialDifference] = []

    @model_validator(mode="after")
    def _derive_verdict(self) -> JudgeVerdict:
        event, payment = self.core_insured_event, self.core_payment_mechanism
        if event in ("narrower_in_a", "narrower_in_b", "different") or payment == "different":
            self.verdict = "different_coverage"
        elif event == "same" and payment == "same":
            self.verdict = "same_coverage"
        else:
            self.verdict = "uncertain"
        self.material_differences = [
            d for d in self.material_differences if not is_vague_difference(d.document_a, d.document_b)
        ]
        return self


class JudgeAnswer(BaseModel):
    """One question-bank answer (core/questions) as the judge sees it."""

    question: str
    status: str
    answer: str | None = None


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
    # v2 (question-bank based) input - when `answers` is non-empty the judge
    # compares these instead of the old extraction fields above, which the
    # 2026-10 review found unreliable (period mix-ups, premium-table ages).
    category_id: str | None = None
    fingerprint: dict[str, str] = {}
    answers: list[JudgeAnswer] = []


_ANSWER_STATUS_HE = {
    "FOUND": "נמצא",
    "NOT_FOUND": "לא נמצא במסמך",
    "NOT_APPLICABLE": "לא רלוונטי",
    "AMBIGUOUS": "לא חד-משמעי",
}
# Keeps a 28-question pair comfortably inside a few thousand tokens.
_MAX_ANSWER_CHARS = 400


def _format_side_v2(label: str, info: DocumentJudgeInfo) -> str:
    lines = [f"=== {label} (חברת {info.company_id}) ==="]
    if info.appendix_name:
        lines.append(f"שם הנספח: {info.appendix_name}")
    if info.category_id:
        lines.append(f"קטגוריה: {info.category_id}")
    if info.fingerprint:
        lines.append("טביעת אצבע: " + "; ".join(f"{k}={v}" for k, v in info.fingerprint.items()))
    lines.append("תשובות לבנק השאלות:")
    for item in info.answers:
        status = _ANSWER_STATUS_HE.get(item.status, item.status)
        answer = " ".join((item.answer or "").split())
        if len(answer) > _MAX_ANSWER_CHARS:
            answer = answer[:_MAX_ANSWER_CHARS] + "…"
        lines.append(f"- {item.question} [{status}]" + (f": {answer}" if answer else ""))
    return "\n".join(lines)


def _format_side(label: str, info: DocumentJudgeInfo) -> str:
    if info.answers:
        return _format_side_v2(label, info)
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
            "temperature": 0,
            "seed": 0,
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
                # Same pair, same answer: a re-run of the cancer pilot's 17
                # dropped pairs flipped 3 verdicts at the default temperature.
                temperature=0,
                seed=0,
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
