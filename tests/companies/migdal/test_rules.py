from __future__ import annotations

from companies.migdal.config import MigdalConfig
from companies.migdal.rules import MigdalRules


def test_page_zero_returns_footer_region() -> None:
    rules = MigdalRules(MigdalConfig())
    regions = rules.get_ocr_crop_regions(0)
    assert regions == [(0.0, 0.85, 1.0, 1.0)]


def test_other_pages_return_no_regions() -> None:
    rules = MigdalRules(MigdalConfig())
    assert rules.get_ocr_crop_regions(1) == []
    assert rules.get_ocr_crop_regions(5) == []
