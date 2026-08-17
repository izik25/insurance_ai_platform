from __future__ import annotations

import pytest

from core.exceptions import ConfigurationError
from core.matching.profiles.registry import get_profile_for_category


def test_default_profile_loads() -> None:
    profile = get_profile_for_category("no.such.category")
    assert profile.category_id == "default"
    assert profile.thresholds.auto_match > profile.thresholds.deep_verification
    assert profile.thresholds.deep_verification > profile.thresholds.ambiguous


def test_default_profile_weights_sum_close_to_one() -> None:
    profile = get_profile_for_category("no.such.category")
    total = sum(fw.weight for fw in profile.feature_weights)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_default_profile_has_hard_constraints() -> None:
    profile = get_profile_for_category("no.such.category")
    assert len(profile.hard_constraints) > 0


def test_unknown_category_falls_back_to_default() -> None:
    fallback = get_profile_for_category("health.surgeries.israel")
    assert fallback.category_id == "default"


def test_category_with_dedicated_profile_does_not_fall_back() -> None:
    profile = get_profile_for_category("health.critical_illness.cancer")
    assert profile.category_id == "health.critical_illness.cancer"
    assert sum(fw.weight for fw in profile.feature_weights) == pytest.approx(1.0, abs=1e-6)
