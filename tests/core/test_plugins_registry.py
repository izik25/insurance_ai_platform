from __future__ import annotations

from pathlib import Path

import pytest

from core.exceptions import CompanyNotRegisteredError, DuplicateCompanyError
from core.plugins.base import (
    BaseDownloader,
    BaseExtractor,
    BaseParser,
    BaseRules,
    CompanyConfig,
    CompanyPlugin,
)
from core.plugins.registry import CompanyRegistry


class _DummyDownloader(BaseDownloader):
    def download_all(self, destination_dir: Path, limit: int | None = None) -> list[Path]:
        return []


class _DummyParser(BaseParser):
    def extract_text(self, file_path: Path) -> str:
        return ""


class _DummyExtractor(BaseExtractor):
    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        return {}


class _DummyRules(BaseRules):
    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        return []


def _make_plugin(company_id: str) -> CompanyPlugin:
    config = CompanyConfig(company_id=company_id, display_name=company_id.title())
    return CompanyPlugin(
        config=config,
        downloader=_DummyDownloader(config),
        parser=_DummyParser(config),
        extractor=_DummyExtractor(config),
        rules=_DummyRules(config),
    )


def test_register_and_get() -> None:
    registry = CompanyRegistry()
    plugin = _make_plugin("dummy")

    registry.register(plugin)

    assert registry.get("dummy") is plugin
    assert registry.list_companies() == ["dummy"]


def test_duplicate_registration_raises() -> None:
    registry = CompanyRegistry()
    registry.register(_make_plugin("dummy"))

    with pytest.raises(DuplicateCompanyError):
        registry.register(_make_plugin("dummy"))


def test_get_unregistered_raises() -> None:
    registry = CompanyRegistry()
    with pytest.raises(CompanyNotRegisteredError):
        registry.get("missing")
