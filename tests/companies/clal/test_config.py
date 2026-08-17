from __future__ import annotations

from companies.clal.config import DOMAIN_TO_FAMILY, ClalConfig


def test_domain_to_family_mapping() -> None:
    assert DOMAIN_TO_FAMILY == {"health": ["1520", "13217"], "life": ["8277"]}


def test_default_config_values() -> None:
    config = ClalConfig()
    assert config.company_id == "clal"
    assert config.display_name == "כלל"
    assert config.company_filter_ids == ["1", "9"]
