from __future__ import annotations

from core.canonical.schema import AgeRestrictions, CanonicalCoverageProfile, DeductibleInfo
from core.fingerprint.builder import build_fingerprint


def _profile(**overrides) -> CanonicalCoverageProfile:
    defaults = dict(
        insured_event="גילוי מחלת הסרטן",
        covered_events=["אירוע א", "אירוע ב"],
        exclusions_normalized=["חריג א", "חריג ב", "חריג ג"],
        limitations=[],
        waiting_period_text="90 יום מתחילת הביטוח",
        qualifying_period_text=None,
        survival_period_text="30 יום",
        benefit_type="פיצוי חד פעמי",
        benefit_calculation="20% מסכום הביטוח",
        amounts=["₪500,000 במקרה של אבחון מחלה קשה"],
        caps=['תקרת השתל 15,000 ש"ח'],
        deductible=DeductibleInfo(applies=True, amount_text="₪25,000", description="השתתפות עצמית"),
        age_restrictions=AgeRestrictions(min_age=18, max_age=65, termination_age=70, description="18-65"),
        special_conditions=["תנאי מיוחד א"],
    )
    defaults.update(overrides)
    return CanonicalCoverageProfile(**defaults)


def test_build_fingerprint_derives_periods_and_amounts() -> None:
    result = build_fingerprint(
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype="CANCER",
        benefit_model="LUMP_SUM",
        target_population="INDIVIDUAL",
        profile=_profile(),
        benefit_type_code="LUMP_SUM",
    )
    assert result.waiting_period_days == 90
    assert result.qualifying_period_days is None
    assert result.survival_period_days == 30
    assert result.benefit_amount_min == 500000.0
    assert result.benefit_amount_currency == "ILS"
    assert result.maximum_benefit == 15000.0
    assert result.deductible_amount == 25000.0
    assert result.benefit_percentage == 20.0
    assert result.benefit_type == "LUMP_SUM"
    assert result.min_entry_age == 18
    assert result.max_entry_age == 65
    assert result.termination_age == 70


def test_build_fingerprint_counts_match_profile_lists() -> None:
    result = build_fingerprint(
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=_profile(),
        benefit_type_code=None,
    )
    assert result.covered_event_count == 2
    assert result.major_exclusion_count == 3
    assert result.special_condition_count == 1


def test_build_fingerprint_falls_back_to_raw_benefit_type_when_no_code() -> None:
    result = build_fingerprint(
        main_category=None,
        coverage_family=None,
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=_profile(benefit_type="ניסוח חופשי לא ידוע"),
        benefit_type_code=None,
    )
    assert result.benefit_type == "ניסוח חופשי לא ידוע"


def test_build_fingerprint_handles_no_deductible() -> None:
    result = build_fingerprint(
        main_category=None,
        coverage_family=None,
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=_profile(deductible=DeductibleInfo(applies=False)),
        benefit_type_code=None,
    )
    assert result.deductible_amount is None


def test_build_fingerprint_is_deterministic() -> None:
    profile = _profile()
    kwargs = dict(
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype="CANCER",
        benefit_model="LUMP_SUM",
        target_population="INDIVIDUAL",
        profile=profile,
        benefit_type_code="LUMP_SUM",
    )
    first = build_fingerprint(**kwargs)
    second = build_fingerprint(**kwargs)
    assert first.as_dict() == second.as_dict()
