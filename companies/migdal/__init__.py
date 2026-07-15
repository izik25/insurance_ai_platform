"""Migdal insurance company plugin — health and life policy-terms archive."""

from __future__ import annotations

from companies.migdal.config import MigdalConfig
from companies.migdal.downloader import MigdalDownloader
from companies.migdal.extractor import MigdalExtractor
from companies.migdal.parser import MigdalParser
from companies.migdal.rules import MigdalRules
from core.config.settings import get_settings
from core.ocr.engine import TesseractEngine
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = MigdalConfig()
    settings = get_settings()
    ocr_engine = TesseractEngine(settings.tessdata_dir, lang=settings.ocr_language)
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=MigdalDownloader(config),
            parser=MigdalParser(config),
            extractor=MigdalExtractor(config, ocr_engine=ocr_engine),
            rules=MigdalRules(config),
        )
    )
