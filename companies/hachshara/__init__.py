"""Hachshara (הכשרה חברה לביטוח בע"מ) insurance company plugin."""

from __future__ import annotations

from companies.hachshara.config import HachsharaConfig
from companies.hachshara.downloader import HachsharaDownloader
from companies.hachshara.extractor import HachsharaExtractor
from companies.hachshara.parser import HachsharaParser
from companies.hachshara.rules import HachsharaRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = HachsharaConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=HachsharaDownloader(config),
            parser=HachsharaParser(config),
            extractor=HachsharaExtractor(config),
            rules=HachsharaRules(config),
        )
    )
