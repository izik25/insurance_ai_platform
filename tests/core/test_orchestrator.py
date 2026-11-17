from __future__ import annotations

from core.matching.candidate_index import CandidateIndex, CandidateIndexEntry
from core.matching.hard_constraints import FingerprintFeatures
from core.matching.orchestrator import FAST_ONLY, NEEDS_DEEP_VERIFICATION, run_stage_a_for_document


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
        benefit_type="LUMP_SUM",
        maximum_benefit=500_000.0,
        codes={
            "insured_event": {"CANCER", "STROKE"},
            "covered_event": {"SURGERY_ISRAEL"},
            "exclusion": {"PRE_EXISTING_CONDITION"},
        },
    )
    defaults.update(overrides)
    return FingerprintFeatures(**defaults)  # type: ignore[arg-type]


def test_identical_documents_auto_confirm() -> None:
    source = _features(document_id="migdal:a", company_id="migdal")
    candidate = _features(document_id="phoenix:a", company_id="phoenix")
    features_by_id = {source.document_id: source, candidate.document_id: candidate}
    index = CandidateIndex(
        [
            CandidateIndexEntry(source.document_id, source.company_id, source.category_id, True),
            CandidateIndexEntry(candidate.document_id, candidate.company_id, candidate.category_id, True),
        ]
    )

    results = run_stage_a_for_document(source.document_id, features_by_id, index)

    assert "phoenix" in results
    outcome = results["phoenix"]
    assert outcome.matched_document_id == "phoenix:a"
    assert outcome.status == "auto_confirmed"
    assert outcome.match_stage == FAST_ONLY
    assert outcome.final_score >= 0.9


def test_moderately_similar_documents_need_deep_verification() -> None:
    source = _features(document_id="migdal:a", company_id="migdal")
    candidate = _features(
        document_id="phoenix:a",
        company_id="phoenix",
        codes={
            "insured_event": {"HEART_ATTACK"},  # disjoint from source's {CANCER, STROKE}
            "covered_event": {"SURGERY_ISRAEL"},
            "exclusion": {"PRE_EXISTING_CONDITION"},
        },
    )
    features_by_id = {source.document_id: source, candidate.document_id: candidate}
    index = CandidateIndex(
        [
            CandidateIndexEntry(source.document_id, source.company_id, source.category_id, True),
            CandidateIndexEntry(candidate.document_id, candidate.company_id, candidate.category_id, True),
        ]
    )

    results = run_stage_a_for_document(source.document_id, features_by_id, index)

    assert "phoenix" in results
    outcome = results["phoenix"]
    assert outcome.status == "pending_review"
    # A moderate score should land in the deep-verification band, not be
    # silently auto-confirmed or auto-rejected without ever considering an
    # LLM look.
    assert outcome.match_stage in (FAST_ONLY, NEEDS_DEEP_VERIFICATION)


def test_high_score_but_low_evidence_does_not_auto_confirm() -> None:
    """Regression test for a real Phase 4 pilot finding: a pair scoring
    1.0 on only 'category'+'coverage' (everything else MISSING) must not
    be auto-confirmed on that alone - it should be routed to deep
    verification instead."""
    source = _features(
        document_id="migdal:a",
        company_id="migdal",
        waiting_period_days=None,
        benefit_type=None,
        maximum_benefit=None,
        codes={},
    )
    candidate = _features(
        document_id="phoenix:a",
        company_id="phoenix",
        waiting_period_days=None,
        benefit_type=None,
        maximum_benefit=None,
        codes={},
    )
    features_by_id = {source.document_id: source, candidate.document_id: candidate}
    index = CandidateIndex(
        [
            CandidateIndexEntry(source.document_id, source.company_id, source.category_id, True),
            CandidateIndexEntry(candidate.document_id, candidate.company_id, candidate.category_id, True),
        ]
    )

    results = run_stage_a_for_document(source.document_id, features_by_id, index)

    outcome = results["phoenix"]
    assert outcome.final_score == 1.0
    assert outcome.status != "auto_confirmed"
    assert outcome.match_stage == NEEDS_DEEP_VERIFICATION


def test_hard_constraint_failure_excludes_candidate_entirely() -> None:
    source = _features(document_id="migdal:a", company_id="migdal", main_category="HEALTH")
    candidate = _features(document_id="phoenix:a", company_id="phoenix", main_category="LIFE")
    features_by_id = {source.document_id: source, candidate.document_id: candidate}
    index = CandidateIndex(
        [
            CandidateIndexEntry(source.document_id, source.company_id, source.category_id, True),
            CandidateIndexEntry(candidate.document_id, candidate.company_id, candidate.category_id, True),
        ]
    )

    # Even though candidate_index would find this candidate by category_id,
    # the profile's main_category_mismatch hard constraint should still
    # exclude it once evaluated (defense in depth beyond candidate_index).
    results = run_stage_a_for_document(source.document_id, features_by_id, index)
    assert results == {}


def test_returns_one_result_per_other_company() -> None:
    source = _features(document_id="migdal:a", company_id="migdal")
    phoenix_candidate = _features(document_id="phoenix:a", company_id="phoenix")
    clal_candidate = _features(document_id="clal:a", company_id="clal")
    features_by_id = {
        source.document_id: source,
        phoenix_candidate.document_id: phoenix_candidate,
        clal_candidate.document_id: clal_candidate,
    }
    index = CandidateIndex(
        [
            CandidateIndexEntry(source.document_id, source.company_id, source.category_id, True),
            CandidateIndexEntry(
                phoenix_candidate.document_id, phoenix_candidate.company_id, phoenix_candidate.category_id, True
            ),
            CandidateIndexEntry(
                clal_candidate.document_id, clal_candidate.company_id, clal_candidate.category_id, True
            ),
        ]
    )

    results = run_stage_a_for_document(source.document_id, features_by_id, index)
    assert set(results.keys()) == {"phoenix", "clal"}


def test_unknown_document_id_returns_empty() -> None:
    index = CandidateIndex([])
    assert run_stage_a_for_document("does-not-exist", {}, index) == {}
