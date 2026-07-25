"""Tests for cross-company matching — no DB, no network, plain vectors."""

from __future__ import annotations

from core.matching.similarity import DocumentMeta, find_cross_company_matches
from core.models.enums import MatchStatus


def test_matches_across_companies_only() -> None:
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [1.0, 0.0],  # identical -> should match migdal:a
        "migdal:b": [0.0, 1.0],
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "phoenix:a": DocumentMeta(company_id="phoenix", domain="health"),
        "migdal:b": DocumentMeta(company_id="migdal", domain="health"),
    }

    matches = find_cross_company_matches(embeddings, meta)
    by_doc = {m.document_id: m for m in matches}

    assert by_doc["migdal:a"].matched_document_id == "phoenix:a"
    assert by_doc["migdal:a"].status == MatchStatus.AUTO_CONFIRMED
    assert by_doc["phoenix:a"].matched_document_id == "migdal:a"


def test_never_matches_within_same_company() -> None:
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "migdal:b": [1.0, 0.0],  # identical, but same company -> must not match each other
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "migdal:b": DocumentMeta(company_id="migdal", domain="health"),
    }

    assert find_cross_company_matches(embeddings, meta) == []


def test_never_matches_across_domains() -> None:
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [1.0, 0.0],
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "phoenix:a": DocumentMeta(company_id="phoenix", domain="life"),
    }

    assert find_cross_company_matches(embeddings, meta) == []


def test_below_threshold_is_pending_review() -> None:
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [0.5, 0.8660254],  # cosine similarity ~0.5, well under 0.95
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "phoenix:a": DocumentMeta(company_id="phoenix", domain="health"),
    }

    matches = find_cross_company_matches(embeddings, meta)
    assert all(m.status == MatchStatus.PENDING_REVIEW for m in matches)


def test_picks_best_match_among_multiple_candidates() -> None:
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:close": [0.99, 0.14106736],  # closer
        "phoenix:far": [0.0, 1.0],  # orthogonal, much worse
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "phoenix:close": DocumentMeta(company_id="phoenix", domain="health"),
        "phoenix:far": DocumentMeta(company_id="phoenix", domain="health"),
    }

    matches = find_cross_company_matches(embeddings, meta)
    by_doc = {m.document_id: m for m in matches}
    assert by_doc["migdal:a"].matched_document_id == "phoenix:close"


def test_auto_confirmed_when_appendix_names_share_a_word() -> None:
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [1.0, 0.0],
    }
    meta = {
        "migdal:a": DocumentMeta(
            company_id="migdal", domain="health", appendix_name="ניתוחים וטיפולים בישראל"
        ),
        "phoenix:a": DocumentMeta(
            company_id="phoenix", domain="health", appendix_name="גילוי נאות - ניתוחים בישראל"
        ),
    }

    matches = find_cross_company_matches(embeddings, meta)
    assert matches[0].status == MatchStatus.AUTO_CONFIRMED


def test_downgraded_to_pending_review_when_no_lexical_overlap() -> None:
    """A high embedding score alone isn't enough: if both documents have
    descriptive metadata (appendix_name/coverage_type/coverage_name) and it
    shares no meaningful word, the match starts as pending_review even
    though its similarity score is above the auto-confirm threshold - this
    is exactly the real-world failure mode found live (two near-empty
    documents embedding to the same generic text and "matching" at ~1.0
    with no actual relationship)."""
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [1.0, 0.0],
    }
    meta = {
        "migdal:a": DocumentMeta(
            company_id="migdal",
            domain="health",
            appendix_name="שאלון בריאות אישי",
            coverage_type="ביטוח חיים",
        ),
        "phoenix:a": DocumentMeta(
            company_id="phoenix",
            domain="health",
            appendix_name="פירוט מסמכים נדרשים",
            coverage_type="ביטוח חיים",
        ),
    }

    matches = find_cross_company_matches(embeddings, meta)
    assert matches[0].similarity_score >= 0.95
    assert matches[0].status == MatchStatus.PENDING_REVIEW


def test_lexical_gate_skipped_when_neither_side_has_metadata() -> None:
    """Callers that don't supply appendix_name/coverage_type/coverage_name
    at all (e.g. this test suite's other cases, or any future caller with
    no descriptive metadata available) must behave exactly as before the
    lexical gate was added - status decided by similarity score alone."""
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [1.0, 0.0],
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "phoenix:a": DocumentMeta(company_id="phoenix", domain="health"),
    }

    matches = find_cross_company_matches(embeddings, meta)
    assert matches[0].status == MatchStatus.AUTO_CONFIRMED


def test_matches_one_per_other_company_not_a_single_global_best() -> None:
    """A document must get a match against every other company, not just
    the single highest-scoring company overall (the bug this guards
    against: a Migdal appendix that best-matches Phoenix would silently
    hide an equally valid match with Clal)."""
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:a": [0.99, 0.14106736],  # slightly closer to migdal:a...
        "clal:a": [0.98, 0.19866933],  # ...but clal:a is still a good match and must not be dropped
    }
    meta = {
        "migdal:a": DocumentMeta(company_id="migdal", domain="health"),
        "phoenix:a": DocumentMeta(company_id="phoenix", domain="health"),
        "clal:a": DocumentMeta(company_id="clal", domain="health"),
    }

    matches = find_cross_company_matches(embeddings, meta)
    matched_companies = {
        meta[m.matched_document_id].company_id for m in matches if m.document_id == "migdal:a"
    }
    assert matched_companies == {"phoenix", "clal"}
