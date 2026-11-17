from __future__ import annotations

from core.matching.candidate_index import CandidateIndex, CandidateIndexEntry


def _index(*entries: CandidateIndexEntry) -> CandidateIndex:
    return CandidateIndex(list(entries))


def test_candidates_for_returns_other_company_same_category() -> None:
    index = _index(
        CandidateIndexEntry("migdal:a", "migdal", "health.critical_illness.general", True),
        CandidateIndexEntry("phoenix:a", "phoenix", "health.critical_illness.general", True),
    )
    candidates = index.candidates_for("migdal:a")
    assert [c.document_id for c in candidates] == ["phoenix:a"]


def test_candidates_for_excludes_same_company() -> None:
    index = _index(
        CandidateIndexEntry("migdal:a", "migdal", "health.critical_illness.general", True),
        CandidateIndexEntry("migdal:b", "migdal", "health.critical_illness.general", True),
    )
    assert index.candidates_for("migdal:a") == []


def test_candidates_for_excludes_different_category() -> None:
    index = _index(
        CandidateIndexEntry("migdal:a", "migdal", "health.critical_illness.general", True),
        CandidateIndexEntry("phoenix:a", "phoenix", "health.surgeries.israel", True),
    )
    assert index.candidates_for("migdal:a") == []


def test_candidates_for_excludes_inactive_candidates() -> None:
    """The whole point of the is_active asymmetry (see
    core/matching/similarity.py's DocumentMeta.is_active): a candidate must
    be active, even though the source itself doesn't have to be."""
    index = _index(
        CandidateIndexEntry("migdal:old", "migdal", "health.critical_illness.general", False),
        CandidateIndexEntry("phoenix:old", "phoenix", "health.critical_illness.general", False),
        CandidateIndexEntry("phoenix:current", "phoenix", "health.critical_illness.general", True),
    )
    # migdal:old is inactive but still a valid SOURCE - it should still
    # find phoenix:current as a candidate, and never phoenix:old.
    candidates = {c.document_id for c in index.candidates_for("migdal:old")}
    assert candidates == {"phoenix:current"}


def test_candidates_for_unknown_document_returns_empty() -> None:
    index = _index(CandidateIndexEntry("migdal:a", "migdal", "health.critical_illness.general", True))
    assert index.candidates_for("does-not-exist") == []


def test_candidates_for_categories_includes_alternative_categories() -> None:
    index = _index(
        CandidateIndexEntry("migdal:a", "migdal", "health.critical_illness.general", True),
        CandidateIndexEntry("phoenix:a", "phoenix", "health.transplants", True),
    )
    candidates = index.candidates_for_categories("migdal:a", ["health.transplants"])
    assert {c.document_id for c in candidates} == {"phoenix:a"}


def test_candidates_for_categories_deduplicates_across_categories() -> None:
    index = _index(
        CandidateIndexEntry("migdal:a", "migdal", "health.critical_illness.general", True),
        CandidateIndexEntry("phoenix:a", "phoenix", "health.critical_illness.general", True),
    )
    candidates = index.candidates_for_categories("migdal:a", ["health.critical_illness.general"])
    assert len(candidates) == 1
