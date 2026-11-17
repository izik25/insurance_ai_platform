"""Hard Constraints: categorical disqualifiers evaluated BEFORE any
quantitative scoring or LLM call, per category matching profile
(core/matching/profiles/). Pure functions, no DB, no network - a candidate
pair either survives (empty list returned) or is dropped from
consideration entirely, never scored.

`FingerprintFeatures` is the shared, DB-independent data shape hard
constraints and core/matching/quantitative_score.py both operate on - a
plain dataclass so both modules (and their tests) never need a live
database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.matching.profiles.schema import HardConstraintRule


@dataclass(frozen=True)
class FingerprintFeatures:
    document_id: str
    company_id: str
    category_id: str
    main_category: str | None
    coverage_family: str | None
    coverage_subtype: str | None
    benefit_model: str | None
    target_population: str | None
    alternative_categories: list[str] = field(default_factory=list)

    waiting_period_days: int | None = None
    qualifying_period_days: int | None = None
    survival_period_days: int | None = None
    min_entry_age: int | None = None
    max_entry_age: int | None = None
    benefit_type: str | None = None
    benefit_amount_min: float | None = None
    benefit_amount_max: float | None = None
    maximum_benefit: float | None = None
    deductible_amount: float | None = None

    # code_category -> set of canonical codes (document_canonical_codes)
    codes: dict[str, set[str]] = field(default_factory=dict)


# Pairs of benefit_model/target_population values that are flatly
# incompatible (not just "different") - a lump-sum critical-illness payout
# can never be the same product as an in-kind service certificate, no
# matter how similar everything else looks. Deliberately short: only
# combinations that are unambiguously wrong belong here, since a false
# positive here silently drops a real candidate before it's ever scored.
_INCOMPATIBLE_BENEFIT_MODELS = {
    frozenset({"LUMP_SUM", "IN_KIND_SERVICE"}),
    frozenset({"REIMBURSEMENT", "IN_KIND_SERVICE"}),
    frozenset({"MONTHLY_ANNUITY", "IN_KIND_SERVICE"}),
}
_INCOMPATIBLE_TARGET_POPULATIONS = {
    frozenset({"CHILDREN", "FOREIGN_WORKERS"}),
}


def _rule_main_category_mismatch(a: FingerprintFeatures, b: FingerprintFeatures) -> bool:
    return bool(a.main_category and b.main_category and a.main_category != b.main_category)


def _rule_coverage_family_mismatch(a: FingerprintFeatures, b: FingerprintFeatures) -> bool:
    if not a.coverage_family or not b.coverage_family or a.coverage_family == b.coverage_family:
        return False
    # Don't reject a pair the taxonomy itself declared as plausibly related -
    # core/taxonomy/data/taxonomy.v1.yaml's alternative_categories exists
    # exactly to avoid a borderline classification silently dropping a real
    # candidate (req: "אם הסיווג אינו חד-משמעי, אפשר לשמור גם
    # alternative_categories כדי לא לפספס התאמה").
    if a.category_id in b.alternative_categories or b.category_id in a.alternative_categories:
        return False
    return True


def _rule_benefit_model_incompatible(a: FingerprintFeatures, b: FingerprintFeatures) -> bool:
    if not a.benefit_model or not b.benefit_model or a.benefit_model == b.benefit_model:
        return False
    return frozenset({a.benefit_model, b.benefit_model}) in _INCOMPATIBLE_BENEFIT_MODELS


def _rule_target_population_incompatible(a: FingerprintFeatures, b: FingerprintFeatures) -> bool:
    if not a.target_population or not b.target_population or a.target_population == b.target_population:
        return False
    return frozenset({a.target_population, b.target_population}) in _INCOMPATIBLE_TARGET_POPULATIONS


_RULE_DISPATCH = {
    "main_category_mismatch": _rule_main_category_mismatch,
    "coverage_family_mismatch": _rule_coverage_family_mismatch,
    "benefit_model_incompatible": _rule_benefit_model_incompatible,
    "target_population_incompatible": _rule_target_population_incompatible,
}


def evaluate_hard_constraints(
    source: FingerprintFeatures, candidate: FingerprintFeatures, rules: list[HardConstraintRule]
) -> list[str]:
    """Returns the ids of every rule that fired - a non-empty list means the
    pair is disqualified and must never reach quantitative_score or an LLM
    call. An unknown `rule.rule` id is skipped (logged nowhere - profile
    YAML validation is a config-authoring concern, not a runtime one)."""
    failed: list[str] = []
    for rule in rules:
        check = _RULE_DISPATCH.get(rule.rule)
        if check is not None and check(source, candidate):
            failed.append(rule.id)
    return failed
