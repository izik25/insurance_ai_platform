"""Pydantic schema for a category matching profile config file."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeatureWeight(BaseModel):
    # One of the score_breakdown dimensions computed by
    # core/matching/quantitative_score.py, e.g. "category", "coverage",
    # "insured_event", "covered_events", "exclusions", "eligibility",
    # "periods", "benefit", "definitions".
    dimension: str
    weight: float
    # Categorical label (CRITICAL/HIGH/MEDIUM/LOW/INFORMATIONAL) - kept
    # separate from `weight`: weight feeds the numeric weighted-average
    # score, importance is what calibration (scripts/calibrate_matching.py)
    # reasons about and reports on, and what marks a dimension as eligible
    # to become a hard constraint (a CRITICAL mismatch can disqualify a
    # candidate outright, independent of the weighted average).
    importance: str = "MEDIUM"


class HardConstraintRule(BaseModel):
    id: str
    description_he: str
    # Identifier consumed by core/matching/hard_constraints.py's rule
    # dispatch - e.g. "main_category_mismatch", "coverage_family_mismatch",
    # "benefit_model_incompatible", "target_population_incompatible",
    # "insured_event_contradiction". Kept a plain string (not an enum) so a
    # new rule type is a data change, not a schema migration.
    rule: str


class MatchThresholds(BaseModel):
    auto_match: float
    deep_verification: float
    ambiguous: float
    reject: float


class MarginRules(BaseModel):
    min_margin_for_high_confidence: float = 0.05


class CategoryMatchingProfile(BaseModel):
    version: str
    # "default" for the fallback profile; a taxonomy category_id otherwise.
    category_id: str
    feature_weights: list[FeatureWeight]
    hard_constraints: list[HardConstraintRule] = Field(default_factory=list)
    thresholds: MatchThresholds
    margin_rules: MarginRules = MarginRules()
