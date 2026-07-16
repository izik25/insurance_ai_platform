from __future__ import annotations

from companies.phoenix.config import DOMAIN_TO_WORLD, PhoenixConfig


def test_domain_to_world_mapping() -> None:
    assert DOMAIN_TO_WORLD == {"health": "HealthInsCovers", "life": "LifeInsCovers"}


def test_default_config_values() -> None:
    config = PhoenixConfig()
    assert config.company_id == "phoenix"
    assert config.display_name == "הפניקס"
    assert config.company_filter == "הפניקס"
