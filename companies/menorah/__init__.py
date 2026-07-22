"""Menorah (מנורה מבטחים) insurance company plugin - health and life policy archive."""

from __future__ import annotations

from companies.menorah.config import MenorahConfig
from companies.menorah.downloader import MenorahDownloader
from companies.menorah.extractor import MenorahExtractor
from companies.menorah.parser import MenorahParser
from companies.menorah.rules import MenorahRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = MenorahConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=MenorahDownloader(config),
            parser=MenorahParser(config),
            extractor=MenorahExtractor(config),
            rules=MenorahRules(config),
        )
    )
