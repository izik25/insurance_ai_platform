"""Direct Insurance (ביטוח ישיר) insurance company plugin - forms/policy archive."""

from __future__ import annotations

from companies.directinsurance.config import DirectInsuranceConfig
from companies.directinsurance.downloader import DirectInsuranceDownloader
from companies.directinsurance.extractor import DirectInsuranceExtractor
from companies.directinsurance.parser import DirectInsuranceParser
from companies.directinsurance.rules import DirectInsuranceRules
from core.plugins.base import CompanyPlugin
from core.plugins.registry import CompanyRegistry


def register(registry: CompanyRegistry) -> None:
    config = DirectInsuranceConfig()
    registry.register(
        CompanyPlugin(
            config=config,
            downloader=DirectInsuranceDownloader(config),
            parser=DirectInsuranceParser(config),
            extractor=DirectInsuranceExtractor(config),
            rules=DirectInsuranceRules(config),
        )
    )
