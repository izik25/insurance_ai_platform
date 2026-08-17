from __future__ import annotations

from companies.hachshara.config import DOMAIN_TO_LISTING_PATH, HachsharaConfig


def test_domain_to_listing_path_mapping() -> None:
    assert DOMAIN_TO_LISTING_PATH == {
        "health": "/file-finder/health-insurance/",
        "life": "/file-finder/life-insurance/",
        "mortgage": "/file-finder/mortgage-insurance/",
    }


def test_default_config_values() -> None:
    config = HachsharaConfig()
    assert config.company_id == "hachshara"
    assert config.display_name == 'הכשרה חברה לביטוח בע"מ'
    assert config.base_url == "https://www.hcsra.co.il"
