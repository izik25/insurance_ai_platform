"""Shared, resilient Batch API polling for the taxonomy/matching upgrade's
new structured-output callers (classification, question-answering,
canonical-profile, canonical-code fallback - core/extraction/llm_extract.py
keeps its own original wait_for_batch, untouched, per the "don't touch
working files" rule).

A bare `client.batches.retrieve(batch_id)` connection blip has now killed a
multi-hour script three separate times in real runs (confirmed live:
httpx.ConnectError / openai.APIConnectionError during a routine 30s poll,
unrelated to the batch's own status - the batch itself was fine, only the
GET to check on it failed). Losing hours of already-submitted, already-
paid-for batch progress to a network hiccup during a status check is worse
than a bug: the batch keeps running on OpenAI's side regardless, so the
right fix is to retry the poll itself, not to let the whole script die and
require a manual restart.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from openai import APIConnectionError, OpenAI

from core.utils.logging import get_logger

logger = get_logger(__name__)

_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
_MAX_CONSECUTIVE_CONNECTION_ERRORS = 8

_T = TypeVar("_T")


def call_with_connection_retry(
    description: str, fn: Callable[[], _T], max_attempts: int = _MAX_CONSECUTIVE_CONNECTION_ERRORS
) -> _T:
    """Retries a single OpenAI SDK call (e.g. `client.files.content(...)`) on
    a connection error, with backoff - the same protection
    `wait_for_batch_resilient` gives the status-polling loop, generalized to
    any other post-batch-completion call. Confirmed live: a batch that
    finished fine can still fail to *fetch* (getaddrinfo/DNS blip on this
    network) right after `wait_for_batch_resilient` successfully saw it
    reach a terminal status - the network problem isn't confined to the
    polling loop, so neither is the fix."""
    consecutive_errors = 0
    while True:
        try:
            return fn()
        except APIConnectionError as exc:
            consecutive_errors += 1
            if consecutive_errors > max_attempts:
                logger.error(
                    "%s: %d consecutive connection errors - giving up.", description, consecutive_errors
                )
                raise
            backoff = min(60.0, 5.0 * consecutive_errors)
            logger.warning(
                "%s: connection error (attempt %d/%d), retrying in %.0fs: %s",
                description,
                consecutive_errors,
                max_attempts,
                backoff,
                exc,
            )
            time.sleep(backoff)


def wait_for_batch_resilient(client: OpenAI, batch_id: str, poll_seconds: float = 30.0) -> None:
    """Blocks until the batch finishes, same contract as the original
    wait_for_batch - but a connection error while polling is retried
    (with backoff) rather than propagated, up to
    _MAX_CONSECUTIVE_CONNECTION_ERRORS in a row. Any *other* exception (an
    actual API error, not a connection problem) still raises immediately -
    this only guards against "couldn't even reach the status endpoint"."""
    consecutive_errors = 0
    while True:
        try:
            batch = client.batches.retrieve(batch_id)
        except APIConnectionError as exc:
            consecutive_errors += 1
            if consecutive_errors > _MAX_CONSECUTIVE_CONNECTION_ERRORS:
                logger.error(
                    "Batch %s: %d consecutive connection errors polling status - giving up. "
                    "The batch itself is unaffected on OpenAI's side; re-running this script "
                    "will resume once connectivity is back.",
                    batch_id,
                    consecutive_errors,
                )
                raise
            backoff = min(60.0, poll_seconds * consecutive_errors)
            logger.warning(
                "Batch %s: connection error polling status (attempt %d/%d), retrying in %.0fs: %s",
                batch_id,
                consecutive_errors,
                _MAX_CONSECUTIVE_CONNECTION_ERRORS,
                backoff,
                exc,
            )
            time.sleep(backoff)
            continue

        consecutive_errors = 0
        if batch.status in _TERMINAL_STATUSES:
            logger.info("Batch %s ended with status=%s", batch_id, batch.status)
            return
        logger.info("Batch %s status=%s, waiting...", batch_id, batch.status)
        time.sleep(poll_seconds)
