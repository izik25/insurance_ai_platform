from __future__ import annotations

from core.matching.hard_constraints import FingerprintFeatures, evaluate_hard_constraints
from core.matching.profiles.schema import HardConstraintRule

_RULES = [
    HardConstraintRule(id="main_category_mismatch", description_he="x", rule="main_category_mismatch"),
    HardConstraintRule(id="coverage_family_mismatch", description_he="x", rule="coverage_family_mismatch"),
    HardConstraintRule(id="benefit_model_incompatible", description_he="x", rule="benefit_model_incompatible"),
    HardConstraintRule(
        id="target_population_incompatible", description_he="x", rule="target_population_incompatible"
    ),
]


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
    )
    defaults.update(overrides)
    return FingerprintFeatures(**defaults)  # type: ignore[arg-type]


def test_no_constraints_fail_for_identical_features() -> None:
    a = _features(document_id="a")
    b = _features(document_id="b")
    assert evaluate_hard_constraints(a, b, _RULES) == []


def test_main_category_mismatch_fires() -> None:
    a = _features(document_id="a", main_category="HEALTH")
    b = _features(document_id="b", main_category="LIFE")
    assert "main_category_mismatch" in evaluate_hard_constraints(a, b, _RULES)


def test_coverage_family_mismatch_fires_without_alternative_link() -> None:
    a = _features(document_id="a", coverage_family="CRITICAL_ILLNESS")
    b = _features(document_id="b", coverage_family="SURGERIES")
    assert "coverage_family_mismatch" in evaluate_hard_constraints(a, b, _RULES)


def test_coverage_family_mismatch_waived_by_alternative_categories() -> None:
    a = _features(
        document_id="a",
        category_id="health.critical_illness.general",
        coverage_family="CRITICAL_ILLNESS",
    )
    b = _features(
        document_id="b",
        category_id="health.transplants",
        coverage_family="TRANSPLANTS",
        alternative_categories=["health.critical_illness.general"],
    )
    assert "coverage_family_mismatch" not in evaluate_hard_constraints(a, b, _RULES)


def test_benefit_model_incompatible_fires_for_known_bad_pair() -> None:
    a = _features(document_id="a", benefit_model="LUMP_SUM")
    b = _features(document_id="b", benefit_model="IN_KIND_SERVICE")
    assert "benefit_model_incompatible" in evaluate_hard_constraints(a, b, _RULES)


def test_benefit_model_different_but_not_incompatible_does_not_fire() -> None:
    a = _features(document_id="a", benefit_model="LUMP_SUM")
    b = _features(document_id="b", benefit_model="REIMBURSEMENT")
    assert "benefit_model_incompatible" not in evaluate_hard_constraints(a, b, _RULES)


def test_target_population_incompatible_fires_for_known_bad_pair() -> None:
    a = _features(document_id="a", target_population="CHILDREN")
    b = _features(document_id="b", target_population="FOREIGN_WORKERS")
    assert "target_population_incompatible" in evaluate_hard_constraints(a, b, _RULES)


def test_missing_values_never_trigger_a_constraint() -> None:
    a = _features(document_id="a", main_category=None, benefit_model=None, target_population=None)
    b = _features(document_id="b")
    assert evaluate_hard_constraints(a, b, _RULES) == []


def test_unknown_rule_id_is_skipped_not_raised() -> None:
    a = _features(document_id="a")
    b = _features(document_id="b")
    weird_rule = HardConstraintRule(id="mystery", description_he="x", rule="something_undefined")
    assert evaluate_hard_constraints(a, b, [weird_rule]) == []
