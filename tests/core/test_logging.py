from __future__ import annotations

import logging

from core.config.settings import Settings
from core.utils.logging import JsonFormatter, configure_logging, get_logger


def test_configure_logging_text_format() -> None:
    configure_logging(Settings(log_level="DEBUG", log_format="text"))
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)


def test_configure_logging_json_format() -> None:
    configure_logging(Settings(log_level="INFO", log_format="json"))
    root = logging.getLogger()
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_get_logger_returns_named_logger() -> None:
    logger = get_logger("insurance_ai_platform.test")
    assert logger.name == "insurance_ai_platform.test"
