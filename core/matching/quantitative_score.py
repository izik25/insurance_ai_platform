"""Quantitative Matching: pure-code, explainable scoring of a candidate
pair from DocumentFingerprint + DocumentCanonicalCode data. No LLM call
anywhere in this module - per req 25/26, the match score should be
computed in code from measurable features, with the LLM reserved for what
can't be reliably normalized (core/matching/semantic_judge.py's role,
wired in by core/matching/orchestrator.py's Stage B, not here).

Every dimension gets an explicit status - SAME / DIFFERENT / MISSING_SOURCE
/ MISSING_TARGET / MISSING_BOTH - never silently treating "both sides are
missing this field" as a match. A MISSING_* dimension is excluded from the
weighted average entirely (the average is renormalized over whatever
dimensions actually have data), rather than defaulting to some score - a
document pair that's missing 5 of 9 dimensions should not end up looking
as confident as one with all 9 present just because the code happens to
average in a neutral value for gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.matching.hard_constraints import FingerprintFeatures
from core.matching.profiles.schema import CategoryMatchingProfile

SAME = "SAME"
DIFFERENT = "DIFFERENT"
MISSING_SOURCE = "MISSING_SOURCE"
MISSING_TARGET = "MISSING_TARGET"
MISSING_BOTH = "MISSING_BOTH"

_MISSING_STATUSES = {MISSING_SOURCE, MISSING_TARGET, MISSING_BOTH}


@dataclass
class DimensionResult:
    dimension: str
    status: str
    score: float | None  # None whenever status is one of _MISSING_STATUSES
    detail: str | None = None


@dataclass
class ScoreResult:
    final_score: float
    dimensions: list[DimensionResult] = field(default_factory=list)
    missing_features: list[str] = field(default_factory=list)
    critical_mismatches: list[str] = field(default_factory=list)
    # weight_total (sum of weights of dimensions that actually had data) /
    # total profile weight - how much of the profile's intended evidence
    # this score is actually based on. A pair missing 7 of 9 dimensions can
    # still average to 1.0 on the 2 that remain (confirmed live: a
    # sparsely-extracted document scored a false 1.0 purely on
    # category+coverage matching, with every code-set/period/benefit
    # dimension MISSING) - final_score alone can't distinguish "confidently
    # identical" from "barely any evidence, what little there is agrees".
    # core/matching/orchestrator.py uses this to keep a low-evidence pair
    # out of auto-confirmation even when the raw score clears the
    # threshold.
    weight_covered_fraction: float = 1.0

    def as_breakdown(self) -> dict:
        """Explainable per-dimension breakdown suitable for
        DocumentMatch.score_breakdown (JSONB) - req 25."""
        breakdown: dict = {
            "final_score": round(self.final_score, 4),
            "weight_covered_fraction": round(self.weight_covered_fraction, 4),
        }
        for d in self.dimensions:
            breakdown[d.dimension] = {
                "score": round(d.score, 4) if d.score is not None else None,
                "status": d.status,
                "detail": d.detail,
            }
        return breakdown


def _jaccard_dimension(dimension: str, a: set[str], b: set[str]) -> DimensionResult:
    if not a and not b:
        return DimensionResult(dimension, MISSING_BOTH, None)
    if not a:
        return DimensionResult(dimension, MISSING_SOURCE, None)
    if not b:
        return DimensionResult(dimension, MISSING_TARGET, None)
    union = a | b
    overlap = a & b
    score = len(overlap) / len(union)
    status = SAME if score == 1.0 else DIFFERENT
    return DimensionResult(dimension, status, score, detail=f"{len(overlap)}/{len(union)} shared codes")


def _numeric_closeness(name: str, a: float | None, b: float | None, scale: float) -> DimensionResult:
    if a is None and b is None:
        return DimensionResult(name, MISSING_BOTH, None)
    if a is None:
        return DimensionResult(name, MISSING_SOURCE, None)
    if b is None:
        return DimensionResult(name, MISSING_TARGET, None)
    diff = abs(a - b)
    score = max(0.0, 1.0 - diff / scale) if scale > 0 else (1.0 if diff == 0 else 0.0)
    status = SAME if a == b else DIFFERENT
    return DimensionResult(name, status, score, detail=f"|{a:g}-{b:g}|={diff:g}")


def _enum_match(
    name: str, a: str | None, b: str | None, compatible: set[frozenset[str]] | None = None
) -> DimensionResult:
    if a is None and b is None:
        return DimensionResult(name, MISSING_BOTH, None)
    if a is None:
        return DimensionResult(name, MISSING_SOURCE, None)
    if b is None:
        return DimensionResult(name, MISSING_TARGET, None)
    if a == b:
        return DimensionResult(name, SAME, 1.0)
    if compatible and frozenset({a, b}) in compatible:
        return DimensionResult(name, DIFFERENT, 0.6, detail="compatible-not-identical")
    return DimensionResult(name, DIFFERENT, 0.0)


def _average_present(results: list[DimensionResult]) -> float | None:
    present = [r.score for r in results if r.score is not None]
    return sum(present) / len(present) if present else None


def compute_quantitative_score(
    source: FingerprintFeatures, candidate: FingerprintFeatures, profile: CategoryMatchingProfile
) -> ScoreResult:
    """Assumes hard constraints already passed (core/matching/hard_constraints.py)
    - this function scores, it doesn't disqualify."""
    dims: list[DimensionResult] = []

    if source.category_id == candidate.category_id:
        dims.append(DimensionResult("category", SAME, 1.0, detail="same category_id"))
    elif source.coverage_family == candidate.coverage_family:
        dims.append(DimensionResult("category", DIFFERENT, 0.7, detail="same family, different subtype"))
    elif source.main_category == candidate.main_category:
        dims.append(DimensionResult("category", DIFFERENT, 0.3, detail="same main_category only"))
    else:
        dims.append(DimensionResult("category", DIFFERENT, 0.0, detail="different main_category"))

    dims.append(_enum_match("coverage", source.benefit_model, candidate.benefit_model))

    for dimension, code_category in (
        ("insured_event", "insured_event"),
        ("covered_events", "covered_event"),
        ("exclusions", "exclusion"),
        ("eligibility", "eligibility"),
        ("definitions", "definition"),
    ):
        dims.append(
            _jaccard_dimension(
                dimension, source.codes.get(code_category, set()), candidate.codes.get(code_category, set())
            )
        )

    period_dims = [
        _numeric_closeness(
            f"periods.{field_name}",
            getattr(source, f"{field_name}_days"),
            getattr(candidate, f"{field_name}_days"),
            365.0,
        )
        for field_name in ("waiting_period", "qualifying_period", "survival_period")
    ]
    periods_avg = _average_present(period_dims)
    dims.append(
        DimensionResult(
            "periods",
            MISSING_BOTH if periods_avg is None else DIFFERENT,
            periods_avg,
            detail="; ".join(f"{d.dimension}={d.status}" for d in period_dims),
        )
    )

    benefit_type_dim = _enum_match("benefit.type", source.benefit_type, candidate.benefit_type)
    source_amount = source.maximum_benefit if source.maximum_benefit is not None else source.benefit_amount_max
    candidate_amount = (
        candidate.maximum_benefit if candidate.maximum_benefit is not None else candidate.benefit_amount_max
    )
    amount_dim = _numeric_closeness("benefit.amount", source_amount, candidate_amount, 100_000.0)
    benefit_avg = _average_present([benefit_type_dim, amount_dim])
    dims.append(
        DimensionResult(
            "benefit",
            MISSING_BOTH if benefit_avg is None else DIFFERENT,
            benefit_avg,
            detail=f"type={benefit_type_dim.status}; amount={amount_dim.status}",
        )
    )

    weight_by_dim = {fw.dimension: fw.weight for fw in profile.feature_weights}
    importance_by_dim = {fw.dimension: fw.importance for fw in profile.feature_weights}

    weighted_sum = 0.0
    weight_total = 0.0
    missing_features: list[str] = []
    critical_mismatches: list[str] = []
    for d in dims:
        weight = weight_by_dim.get(d.dimension)
        if weight is None:
            continue
        if d.score is None:
            missing_features.append(d.dimension)
            continue
        weighted_sum += weight * d.score
        weight_total += weight
        if importance_by_dim.get(d.dimension) == "CRITICAL" and d.score < 0.2:
            critical_mismatches.append(d.dimension)

    final_score = weighted_sum / weight_total if weight_total > 0 else 0.0
    total_profile_weight = sum(weight_by_dim.values())
    weight_covered_fraction = weight_total / total_profile_weight if total_profile_weight > 0 else 0.0

    return ScoreResult(
        final_score=final_score,
        dimensions=dims,
        missing_features=missing_features,
        critical_mismatches=critical_mismatches,
        weight_covered_fraction=weight_covered_fraction,
    )
