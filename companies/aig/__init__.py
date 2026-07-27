"""AIG insurance company plugin — health/life policy-terms archive."""

from __future__ import annotations

from companies.aig.config import AigConfig
from companies.aig.downloader import AigDownloader
from companies.aig.extractor import AigExtractor
from companies.aig.parser import AigParser
from companies.aig.rules import AigRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = AigConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=AigDownloader(config),
            parser=AigParser(config),
            extractor=AigExtractor(config),
            rules=AigRules(config),
        )
    )
