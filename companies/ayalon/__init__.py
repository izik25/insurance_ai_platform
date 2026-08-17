"""Ayalon (איילון) insurance company plugin - health and life policy archive."""

from __future__ import annotations

from companies.ayalon.config import AyalonConfig
from companies.ayalon.downloader import AyalonDownloader
from companies.ayalon.extractor import AyalonExtractor
from companies.ayalon.parser import AyalonParser
from companies.ayalon.rules import AyalonRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = AyalonConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=AyalonDownloader(config),
            parser=AyalonParser(config),
            extractor=AyalonExtractor(config),
            rules=AyalonRules(config),
        )
    )
