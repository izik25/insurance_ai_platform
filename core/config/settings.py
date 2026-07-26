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
    # A malformed/huge scanned page can hang the tesseract subprocess
    # indefinitely (confirmed live: a real extraction run sat blocked for
    # ~12 hours at ~0% CPU on one page, with no exception ever raised to
    # trigger the existing per-document error handling). This timeout turns
    # a hang into a catchable OcrError instead.
    ocr_timeout_seconds: float = 120.0

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
    # 0.90, not the more obvious 0.95: with lexical corroboration mandatory
    # for every auto-confirm regardless of this threshold (see
    # core/matching/similarity._lexically_corroborated), raw embedding
    # score alone no longer has to carry the whole precision burden. Sampled
    # live against the real corpus: pairs scoring 0.90-0.95 that also pass
    # lexical corroboration are genuine matches (~90% in a 20-pair sample,
    # e.g. "אובדן כושר עבודה" / "סיעוד" / "מחלות קשות" pairs phrased
    # differently per company) - keeping the bar at 0.95 was leaving
    # thousands of these sitting in pending_review for no real precision
    # gain. Below 0.90 was not sampled - left untouched.
    similarity_auto_confirm_threshold: float = 0.90

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
