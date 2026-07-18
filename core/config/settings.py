"""Centralized application settings, loaded from environment variables / .env.

Every other module reads configuration through `get_settings()` instead of
touching `os.environ` directly, so the source of configuration can change
(e.g. a secrets manager, once this becomes a SaaS) without callers changing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Insurance AI Platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Insurance AI Platform"
    env: str = "development"

    log_level: str = "INFO"
    log_format: str = "text"

    data_dir: Path = Path("./data")
    storage_backend: str = "local"

    database_url: str | None = None

    tessdata_dir: Path = Path("./tessdata")
    ocr_language: str = "heb"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    # OpenAI, not Anthropic: the user's Anthropic org hit an account-level
    # identity-verification block that neither an API key nor OAuth login
    # could get past (confirmed live, repeatedly) - OpenAI is a separate
    # account. extraction_model stays a plain string so switching provider
    # again later is just a config change plus swapping core/extraction/
    # llm_extract.py's client.
    extraction_model: str = "gpt-4.1-mini"
    embedding_model_name: str = "intfloat/multilingual-e5-large"
    similarity_auto_confirm_threshold: float = 0.95

    @property
    def raw_documents_dir(self) -> Path:
        return self.data_dir / "raw_documents"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def json_dictionary_dir(self) -> Path:
        return self.data_dir / "json_dictionary"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (cached after first call)."""
    return Settings()
