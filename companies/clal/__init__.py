"""Clal (כלל) insurance company plugin - health and life policy-terms archive."""

from __future__ import annotations

from companies.clal.config import ClalConfig
from companies.clal.downloader import ClalDownloader
from companies.clal.extractor import ClalExtractor
from companies.clal.parser import ClalParser
from companies.clal.rules import ClalRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = ClalConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=ClalDownloader(config),
            parser=ClalParser(config),
            extractor=ClalExtractor(config),
            rules=ClalRules(config),
        )
    )
