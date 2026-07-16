"""Phoenix (הפניקס) insurance company plugin — health and life policy-terms archive."""

from __future__ import annotations

from companies.phoenix.config import PhoenixConfig
from companies.phoenix.downloader import PhoenixDownloader
from companies.phoenix.extractor import PhoenixExtractor
from companies.phoenix.parser import PhoenixParser
from companies.phoenix.rules import PhoenixRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = PhoenixConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=PhoenixDownloader(config),
            parser=PhoenixParser(config),
            extractor=PhoenixExtractor(config),
            rules=PhoenixRules(config),
        )
    )
