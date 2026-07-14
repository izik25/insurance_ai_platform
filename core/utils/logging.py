"""Structured logging setup.

`configure_logging()` is called once at process startup (see main.py).
Every module then obtains a logger via `get_logger(__name__)`, which is
already wired to the configured handler and format.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from core.config.settings import Settings

_TEXT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


class JsonFormatter(logging.Formatter):
    """Renders each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    """(Re)configure the root logger's level and handler based on settings.

    Safe to call more than once (e.g. across tests) — it always replaces
    the root logger's handlers rather than accumulating duplicates.
    """
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger. Call configure_logging() first."""
    return logging.getLogger(name)
