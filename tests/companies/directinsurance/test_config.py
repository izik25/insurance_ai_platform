from __future__ import annotations

from companies.directinsurance.config import DOMAIN_TO_PRODUCT, DirectInsuranceConfig


def test_domain_to_product_mapping() -> None:
    assert DOMAIN_TO_PRODUCT == {"life": "7"}


def test_default_config_values() -> None:
    config = DirectInsuranceConfig()
    assert config.company_id == "directinsurance"
    assert config.display_name == "ביטוח ישיר"
    assert config.download_url_template.endswith("openform/{form_id}")
