from __future__ import annotations

import pytest

from core.config.settings import get_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)
    settings = get_settings()
    assert settings.app_name == "Insurance AI Platform"
    assert settings.env == "development"
    assert settings.storage_backend == "local"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test Platform")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = get_settings()
    assert settings.app_name == "Test Platform"
    assert settings.log_level == "DEBUG"


def test_computed_data_subdirs() -> None:
    settings = get_settings()
    assert settings.raw_documents_dir == settings.data_dir / "raw_documents"
    assert settings.processed_dir == settings.data_dir / "processed"
    assert settings.json_dictionary_dir == settings.data_dir / "json_dictionary"
