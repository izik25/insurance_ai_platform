from __future__ import annotations

from companies.aig.config import DOMAIN_TO_PAGE_URL, AigConfig


def test_domain_to_page_url_mapping() -> None:
    assert DOMAIN_TO_PAGE_URL == {
        "health": "https://www.aig.co.il/health-insurance/",
        "life": "https://www.aig.co.il/life-insurance/",
    }


def test_default_config_values() -> None:
    config = AigConfig()
    assert config.company_id == "aig"
    assert config.display_name == "AIG"
