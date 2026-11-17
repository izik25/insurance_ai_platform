from __future__ import annotations

import pytest

from core.matching.hard_constraints import FingerprintFeatures
from core.matching.profiles.schema import CategoryMatchingProfile, FeatureWeight, MatchThresholds
from core.matching.quantitative_score import (
    DIFFERENT,
    MISSING_BOTH,
    MISSING_SOURCE,
    MISSING_TARGET,
    SAME,
    compute_quantitative_score,
)


def _profile() -> CategoryMatchingProfile:
    return CategoryMatchingProfile(
        version="v1",
        category_id="health.critical_illness.general",
        feature_weights=[
            FeatureWeight(dimension="category", weight=0.2, importance="CRITICAL"),
            FeatureWeight(dimension="coverage", weight=0.1, importance="HIGH"),
            FeatureWeight(dimension="insured_event", weight=0.2, importance="CRITICAL"),
            FeatureWeight(dimension="covered_events", weight=0.15, importance="HIGH"),
            FeatureWeight(dimension="exclusions", weight=0.1, importance="MEDIUM"),
            FeatureWeight(dimension="eligibility", weight=0.05, importance="MEDIUM"),
            FeatureWeight(dimension="periods", weight=0.05, importance="MEDIUM"),
            FeatureWeight(dimension="benefit", weight=0.1, importance="HIGH"),
            FeatureWeight(dimension="definitions", weight=0.05, importance="LOW"),
        ],
        thresholds=MatchThresholds(auto_match=0.9, deep_verification=0.75, ambiguous=0.6, reject=0.6),
    )


def _features(**overrides: object) -> FingerprintFeatures:
    defaults: dict[str, object] = dict(
        document_id="doc",
        company_id="migdal",
        category_id="health.critical_illness.general",
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype="GENERAL",
        benefit_model="LUMP_SUM",
        target_population="INDIVIDUAL",
        alternative_categories=[],
        waiting_period_days=90,
        qualifying_period_days=90,
        survival_period_days=30,
        benefit_type="LUMP_SUM",
        maximum_benefit=500_000.0,
        codes={
            "insured_event": {"CANCER", "STROKE"},
            "covered_event": {"SURGERY_ISRAEL"},
            "exclusion": {"PRE_EXISTING_CONDITION"},
            "definition": {"SURGERY"},
        },
    )
    defaults.update(overrides)
    return FingerprintFeatures(**defaults)  # type: ignore[arg-type]


def test_identical_documents_score_near_one() -> None:
    a = _features(document_id="a")
    b = _features(document_id="b")
    result = compute_quantitative_score(a, b, _profile())
    assert result.final_score == pytest.approx(1.0, abs=1e-6)
    assert result.critical_mismatches == []


def test_completely_disjoint_codes_lower_the_score() -> None:
    a = _features(document_id="a")
    b = _features(
        document_id="b",
        codes={
            "insured_event": {"HEART_ATTACK"},
            "covered_event": {"AMBULATORY_SERVICE"},
            "exclusion": {"WAR_OR_TERROR"},
            "definition": set(),
        },
    )
    result = compute_quantitative_score(a, b, _profile())
    assert result.final_score < 0.7
    assert "insured_event" in result.critical_mismatches


def test_same_category_id_scores_full_category_dimension() -> None:
    a = _features(document_id="a", category_id="health.critical_illness.general")
    b = _features(document_id="b", category_id="health.critical_illness.general")
    result = compute_quantitative_score(a, b, _profile())
    category_dim = next(d for d in result.dimensions if d.dimension == "category")
    assert category_dim.status == SAME
    assert category_dim.score == 1.0


def test_same_family_different_subtype_scores_partial_category() -> None:
    a = _features(document_id="a", category_id="health.critical_illness.general", coverage_subtype="GENERAL")
    b = _features(document_id="b", category_id="health.critical_illness.cancer", coverage_subtype="CANCER")
    result = compute_quantitative_score(a, b, _profile())
    category_dim = next(d for d in result.dimensions if d.dimension == "category")
    assert category_dim.status == DIFFERENT
    assert category_dim.score == 0.7


def test_missing_both_sides_excluded_from_weighted_average() -> None:
    """The critical requirement: missing-in-both must never silently
    inflate the score by being treated as a match."""
    a = _features(document_id="a", codes={})
    b = _features(document_id="b", codes={})
    result = compute_quantitative_score(a, b, _profile())
    insured_event_dim = next(d for d in result.dimensions if d.dimension == "insured_event")
    assert insured_event_dim.status == MISSING_BOTH
    assert insured_event_dim.score is None
    assert "insured_event" in result.missing_features
    # category/coverage/periods/benefit still present -> score isn't zero
    assert result.final_score > 0


def test_missing_source_vs_missing_target_are_distinguished() -> None:
    a = _features(document_id="a", codes={"insured_event": set()})
    b = _features(document_id="b", codes={"insured_event": {"CANCER"}})
    result = compute_quantitative_score(a, b, _profile())
    insured_event_dim = next(d for d in result.dimensions if d.dimension == "insured_event")
    assert insured_event_dim.status == MISSING_SOURCE

    result_reversed = compute_quantitative_score(b, a, _profile())
    reversed_dim = next(d for d in result_reversed.dimensions if d.dimension == "insured_event")
    assert reversed_dim.status == MISSING_TARGET


def test_period_closeness_scores_partial_for_near_values() -> None:
    a = _features(document_id="a", waiting_period_days=90, qualifying_period_days=None, survival_period_days=None)
    b = _features(document_id="b", waiting_period_days=100, qualifying_period_days=None, survival_period_days=None)
    result = compute_quantitative_score(a, b, _profile())
    periods_dim = next(d for d in result.dimensions if d.dimension == "periods")
    assert 0.9 < periods_dim.score < 1.0


def test_final_score_is_deterministic() -> None:
    a = _features(document_id="a")
    b = _features(document_id="b")
    profile = _profile()
    first = compute_quantitative_score(a, b, profile)
    second = compute_quantitative_score(a, b, profile)
    assert first.final_score == second.final_score
    assert first.as_breakdown() == second.as_breakdown()


def test_weight_covered_fraction_is_full_when_everything_present() -> None:
    a = _features(document_id="a", codes={**_features().codes, "eligibility": {"MINIMUM_ENTRY_AGE"}})
    b = _features(document_id="b", codes={**_features().codes, "eligibility": {"MINIMUM_ENTRY_AGE"}})
    result = compute_quantitative_score(a, b, _profile())
    assert result.weight_covered_fraction == pytest.approx(1.0, abs=1e-6)


def test_weight_covered_fraction_drops_when_most_dimensions_missing() -> None:
    """Regression test for a real finding from the Phase 4 pilot smoke
    test: a sparsely-extracted document scored a false final_score=1.0
    using only 'category' + 'coverage' (both happened to match) while
    every code-set/period/benefit dimension was MISSING. That pair must be
    flagged as low-coverage so the caller (orchestrator) doesn't trust the
    score blindly."""
    a = _features(
        document_id="a",
        waiting_period_days=None,
        qualifying_period_days=None,
        survival_period_days=None,
        benefit_type=None,
        maximum_benefit=None,
        codes={},
    )
    b = _features(
        document_id="b",
        waiting_period_days=None,
        qualifying_period_days=None,
        survival_period_days=None,
        benefit_type=None,
        maximum_benefit=None,
        codes={},
    )
    result = compute_quantitative_score(a, b, _profile())
    # only "category" (0.2) + "coverage" (0.1) weights are present out of 1.0 total
    assert result.final_score == pytest.approx(1.0, abs=1e-6)
    assert result.weight_covered_fraction == pytest.approx(0.3, abs=1e-6)


def test_incompatible_benefit_type_lowers_benefit_dimension() -> None:
    a = _features(document_id="a", benefit_type="LUMP_SUM", maximum_benefit=500_000.0)
    b = _features(document_id="b", benefit_type="REIMBURSEMENT", maximum_benefit=500_000.0)
    result = compute_quantitative_score(a, b, _profile())
    benefit_dim = next(d for d in result.dimensions if d.dimension == "benefit")
    # type differs (0.0) but amount is identical (1.0) -> averages to 0.5
    assert benefit_dim.score == pytest.approx(0.5, abs=1e-6)
