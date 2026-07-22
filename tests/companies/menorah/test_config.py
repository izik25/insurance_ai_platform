from __future__ import annotations

from companies.menorah.config import DOMAIN_TO_LINES_OF_BUSINESS, MenorahConfig


def test_domain_to_lines_of_business_mapping() -> None:
    assert set(DOMAIN_TO_LINES_OF_BUSINESS.keys()) == {"health", "life"}
    health_ids = {lob_id for lob_id, _label in DOMAIN_TO_LINES_OF_BUSINESS["health"]}
    life_ids = {lob_id for lob_id, _label in DOMAIN_TO_LINES_OF_BUSINESS["life"]}
    assert health_ids == {3, 5, 6, 13}
    assert life_ids == {15, 16, 17, 18}
    # No id should be double-booked across domains.
    assert health_ids.isdisjoint(life_ids)


def test_default_config_values() -> None:
    config = MenorahConfig()
    assert config.company_id == "menorah"
    assert config.display_name == "מנורה מבטחים"
