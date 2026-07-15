"""Migdal insurance company plugin — health and life policy-terms archive."""

from __future__ import annotations

from companies.migdal.config import MigdalConfig
from companies.migdal.downloader import MigdalDownloader
from companies.migdal.extractor import MigdalExtractor
from companies.migdal.parser import MigdalParser
from companies.migdal.rules import MigdalRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = MigdalConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=MigdalDownloader(config),
            parser=MigdalParser(config),
            extractor=MigdalExtractor(config),
            rules=MigdalRules(config),
        )
    )
