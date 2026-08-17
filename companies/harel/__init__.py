"""Harel (הראל) insurance company plugin - health and life policy archive."""

from __future__ import annotations

from companies.harel.config import HarelConfig
from companies.harel.downloader import HarelDownloader
from companies.harel.extractor import HarelExtractor
from companies.harel.parser import HarelParser
from companies.harel.rules import HarelRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = HarelConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=HarelDownloader(config),
            parser=HarelParser(config),
            extractor=HarelExtractor(config),
            rules=HarelRules(config),
        )
    )
