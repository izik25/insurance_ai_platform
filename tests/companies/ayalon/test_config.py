from __future__ import annotations

from companies.ayalon.config import (
    CATEGORY_TO_DOMAIN,
    COLLECTIVE_CATEGORY_NAME,
    COLLECTIVE_SUBJECT_TO_DOMAIN,
    AyalonConfig,
)


def test_category_to_domain_mapping() -> None:
    assert set(CATEGORY_TO_DOMAIN.values()) == {"health", "life"}
    assert CATEGORY_TO_DOMAIN["ביטוח חיים"] == "life"
    assert CATEGORY_TO_DOMAIN["ביטוח מחלות קשות"] == "health"
    # Collective is handled separately, never through this mapping.
    assert COLLECTIVE_CATEGORY_NAME not in CATEGORY_TO_DOMAIN


def test_collective_subject_to_domain_excludes_home_and_car() -> None:
    assert set(COLLECTIVE_SUBJECT_TO_DOMAIN.keys()) == {"חיים", "בריאות", "תאונות אישיות"}
    assert "דירה" not in COLLECTIVE_SUBJECT_TO_DOMAIN
    assert "רכב" not in COLLECTIVE_SUBJECT_TO_DOMAIN


def test_default_config_values() -> None:
    config = AyalonConfig()
    assert config.company_id == "ayalon"
    assert config.display_name == "איילון"
