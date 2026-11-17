"""Two-Stage Matching Engine orchestrator.

Stage A (fast, always runs, no LLM): candidate_index narrows the search to
the source document's own category, hard_constraints disqualifies
categorically-incompatible candidates, quantitative_score produces an
explainable per-dimension score for the rest.

Stage B (deep verification) is NOT run inside this module - it needs an
OpenAI client / event loop, which belongs to the calling script
(scripts/match_documents_v2.py), reusing the EXISTING
core.matching.semantic_judge.judge_pairs_concurrent unmodified. This module
only decides WHICH pairs need it (`match_stage == "needs_deep_verification"`)
and hands the caller everything required to build the judge's input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.matching.candidate_index import CandidateIndex
from core.matching.hard_constraints import FingerprintFeatures, evaluate_hard_constraints
from core.matching.profiles.registry import get_profile_for_category
from core.matching.quantitative_score import ScoreResult, compute_quantitative_score

FAST_ONLY = "fast_only"
NEEDS_DEEP_VERIFICATION = "needs_deep_verification"
DEEP_VERIFIED = "deep_verified"

# Below this fraction of a category profile's total dimension weight
# actually having data, a pair is never auto-confirmed on score alone -
# confirmed live in the Phase 4 pilot smoke test: a sparsely-extracted
# document (2 canonical-code rows total, everything else MISSING) scored a
# false final_score=1.0 purely from "same category" + "same benefit_model"
# once every code-set/period/benefit dimension dropped out of the
# renormalized average. A high score built on little evidence is exactly
# the "hard to normalize reliably" case the LLM (Stage B) exists for, so a
# low-coverage pair is routed there instead of being trusted blindly.
_MIN_WEIGHT_COVERAGE_FOR_AUTO_CONFIRM = 0.5


@dataclass
class MatchOutcome:
    document_id: str
    matched_document_id: str
    company_id: str
    final_score: float
    score_breakdown: dict
    critical_mismatches: list[str]
    missing_features: list[str]
    best_candidate_score: float
    second_candidate_score: float | None
    candidate_margin: float | None
    match_stage: str
    status: str
    matching_profile_version: str
    hard_constraint_failures: list[str] = field(default_factory=list)
    # Populated only if Stage B (LLM deep verification, wired in by
    # scripts/match_documents_v2.py using the existing
    # core.matching.semantic_judge) actually ran for this outcome.
    auditor_verdict: str | None = None
    auditor_reasoning: str | None = None
    # [{topic, document_a, document_b}] - the judge's list of material
    # differences between the two documents (A = document_id side).
    material_differences: list[dict] | None = None


def run_stage_a_for_document(
    document_id: str,
    features_by_id: dict[str, FingerprintFeatures],
    index: CandidateIndex,
    auto_confirm_on_score: bool = False,
    min_score_for_judge: float = 0.0,
) -> dict[str, MatchOutcome]:
    """Returns company_id -> best MatchOutcome for `document_id` - at most
    one per other company present among its category-scoped, hard-
    constraint-surviving candidates (mirrors the existing
    core.matching.similarity.find_cross_company_matches "one match per
    other company, not a single global best" design, for the same reason:
    a good match with company B must never be hidden just because company
    A's candidate happened to score slightly higher)."""
    source = features_by_id.get(document_id)
    if source is None:
        return {}
    profile = get_profile_for_category(source.category_id)

    candidates = index.candidates_for(document_id)
    by_company: dict[str, list[tuple[FingerprintFeatures, ScoreResult]]] = {}
    for entry in candidates:
        candidate = features_by_id.get(entry.document_id)
        if candidate is None:
            continue
        if evaluate_hard_constraints(source, candidate, profile.hard_constraints):
            continue
        score_result = compute_quantitative_score(source, candidate, profile)
        by_company.setdefault(entry.company_id, []).append((candidate, score_result))

    results: dict[str, MatchOutcome] = {}
    for company_id, scored in by_company.items():
        scored.sort(key=lambda pair: pair[1].final_score, reverse=True)
        best_candidate, best_score = scored[0]
        second_score = scored[1][1].final_score if len(scored) > 1 else None
        margin = (best_score.final_score - second_score) if second_score is not None else None

        thresholds = profile.thresholds
        if not auto_confirm_on_score:
            # Default since the 2026-11 cancer pilot: the score only ranks and
            # filters candidates; every candidate above the ambiguous band is
            # confirmed or dropped by the LLM judge comparing both documents'
            # question-bank answers. Score-only auto-confirmation produced
            # 1.0s built on a single shared code per dimension, and passed a
            # breast/uterine-only appendix as a general-cancer match at 0.87.
            # No score floor by default: on the 45-appendix pilot the
            # code-overlap score (0.42-0.59) hid the only same-category
            # candidates of 4 appendices from the judge entirely.
            if best_score.final_score < min_score_for_judge:
                continue
            status, stage = "pending_review", NEEDS_DEEP_VERIFICATION
        elif (
            best_score.final_score >= thresholds.auto_match
            and best_score.weight_covered_fraction >= _MIN_WEIGHT_COVERAGE_FOR_AUTO_CONFIRM
        ):
            status, stage = "auto_confirmed", FAST_ONLY
        elif best_score.final_score >= thresholds.auto_match:
            # High score, but on too little evidence to trust blindly -
            # send to the LLM instead of either auto-confirming or
            # under-selling it as merely "ambiguous".
            status, stage = "pending_review", NEEDS_DEEP_VERIFICATION
        elif best_score.final_score >= thresholds.deep_verification:
            status, stage = "pending_review", NEEDS_DEEP_VERIFICATION
        elif best_score.final_score >= thresholds.ambiguous:
            status, stage = "pending_review", FAST_ONLY
        else:
            continue  # below reject threshold - not recorded at all

        results[company_id] = MatchOutcome(
            document_id=document_id,
            matched_document_id=best_candidate.document_id,
            company_id=company_id,
            final_score=best_score.final_score,
            score_breakdown=best_score.as_breakdown(),
            critical_mismatches=best_score.critical_mismatches,
            missing_features=best_score.missing_features,
            best_candidate_score=best_score.final_score,
            second_candidate_score=second_score,
            candidate_margin=margin,
            match_stage=stage,
            status=status,
            matching_profile_version=profile.version,
        )
    return results


def run_stage_a(
    features_by_id: dict[str, FingerprintFeatures],
    index: CandidateIndex,
    auto_confirm_on_score: bool = False,
    min_score_for_judge: float = 0.0,
) -> list[MatchOutcome]:
    """Stage A for every document in `features_by_id` - the full-corpus (or
    full-scope) entry point scripts/match_documents_v2.py calls."""
    outcomes: list[MatchOutcome] = []
    for document_id in features_by_id:
        for outcome in run_stage_a_for_document(
            document_id, features_by_id, index, auto_confirm_on_score, min_score_for_judge
        ).values():
            outcomes.append(outcome)
    return outcomes
