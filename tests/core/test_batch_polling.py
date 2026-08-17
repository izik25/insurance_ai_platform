from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai import APIConnectionError

from core.extraction.batch_polling import (
    _MAX_CONSECUTIVE_CONNECTION_ERRORS,
    call_with_connection_retry,
    wait_for_batch_resilient,
)


def _connection_error() -> APIConnectionError:
    request = MagicMock()
    return APIConnectionError(request=request)


def test_returns_immediately_when_batch_already_terminal() -> None:
    client = MagicMock()
    client.batches.retrieve.return_value = SimpleNamespace(status="completed")

    wait_for_batch_resilient(client, "batch_1", poll_seconds=0)

    client.batches.retrieve.assert_called_once_with("batch_1")


def test_retries_past_a_connection_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.extraction.batch_polling.time.sleep", lambda _s: None)
    client = MagicMock()
    client.batches.retrieve.side_effect = [
        _connection_error(),
        _connection_error(),
        SimpleNamespace(status="completed"),
    ]

    wait_for_batch_resilient(client, "batch_1", poll_seconds=0)

    assert client.batches.retrieve.call_count == 3


def test_polls_again_when_status_not_yet_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.extraction.batch_polling.time.sleep", lambda _s: None)
    client = MagicMock()
    client.batches.retrieve.side_effect = [
        SimpleNamespace(status="in_progress"),
        SimpleNamespace(status="completed"),
    ]

    wait_for_batch_resilient(client, "batch_1", poll_seconds=0)

    assert client.batches.retrieve.call_count == 2


def test_gives_up_after_too_many_consecutive_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.extraction.batch_polling.time.sleep", lambda _s: None)
    client = MagicMock()
    client.batches.retrieve.side_effect = [_connection_error()] * (
        _MAX_CONSECUTIVE_CONNECTION_ERRORS + 1
    )

    with pytest.raises(APIConnectionError):
        wait_for_batch_resilient(client, "batch_1", poll_seconds=0)

    assert client.batches.retrieve.call_count == _MAX_CONSECUTIVE_CONNECTION_ERRORS + 1


def test_call_with_connection_retry_returns_value_on_first_success() -> None:
    result = call_with_connection_retry("test call", lambda: 42)
    assert result == 42


def test_call_with_connection_retry_retries_past_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.extraction.batch_polling.time.sleep", lambda _s: None)
    attempts = [0]

    def flaky() -> str:
        attempts[0] += 1
        if attempts[0] < 3:
            raise _connection_error()
        return "ok"

    result = call_with_connection_retry("flaky call", flaky)
    assert result == "ok"
    assert attempts[0] == 3


def test_call_with_connection_retry_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.extraction.batch_polling.time.sleep", lambda _s: None)

    def always_fails() -> None:
        raise _connection_error()

    with pytest.raises(APIConnectionError):
        call_with_connection_retry("always fails", always_fails, max_attempts=2)


def test_error_count_resets_after_a_successful_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection error followed by a successful (non-terminal) poll must
    reset the consecutive-error counter - only genuinely CONSECUTIVE
    failures should trip the give-up threshold, not an occasional blip
    scattered across an hours-long run."""
    monkeypatch.setattr("core.extraction.batch_polling.time.sleep", lambda _s: None)
    client = MagicMock()
    # One error, one healthy poll, then _MAX again (would exceed the
    # threshold if the counter didn't reset) followed by success.
    side_effects = (
        [_connection_error(), SimpleNamespace(status="in_progress")]
        + [_connection_error()] * _MAX_CONSECUTIVE_CONNECTION_ERRORS
        + [SimpleNamespace(status="completed")]
    )
    client.batches.retrieve.side_effect = side_effects

    wait_for_batch_resilient(client, "batch_1", poll_seconds=0)

    assert client.batches.retrieve.call_count == len(side_effects)
