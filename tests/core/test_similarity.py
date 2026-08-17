"""Tests for cross-company matching — no DB, no network, plain vectors."""

from __future__ import annotations

from core.matching.similarity import DocumentMeta, find_cross_company_matches, rank_candidates_by_company
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


def test_excluded_entirely_when_no_lexical_overlap() -> None:
    """A high embedding score alone isn't enough: if both documents have
    descriptive metadata (appendix_name/coverage_type/coverage_name) and it
    shares no meaningful word, no match is recorded at all - not even as
    pending_review - even though its similarity score is above the
    auto-confirm threshold. This is exactly the real-world failure mode
    found live (two near-empty documents embedding to the same generic text
    and "matching" at ~1.0 with no actual relationship): "definitely
    unrelated" shouldn't occupy a slot in the pending-review queue the way
    "maybe related, needs a human look" should."""
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

    assert find_cross_company_matches(embeddings, meta) == []


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


def test_employer_scoped_policy_excluded_against_general_product() -> None:
    """Regression test for a real corpus finding: a Clal group disability
    policy restricted to Bank Hapoalim employees only was the best
    cross-company match for 263 unrelated, general-population disability
    appendices (auto-confirming 257 of them) - identical high embedding
    score and a shared word ("אובדן כושר עבודה") aren't enough; a policy
    scoped to one employer must not corroborate against a product anyone
    can buy. With only these two candidates, and no other Migdal document
    for this document to fall back to, no match is recorded at all."""
    embeddings = {
        "clal:a": [1.0, 0.0],
        "migdal:a": [1.0, 0.0],
    }
    meta = {
        "clal:a": DocumentMeta(
            company_id="clal",
            domain="life",
            appendix_name="הסכם אובדן כושר עבודה לעובדי בנק הפועלים",
            coverage_type="אובדן כושר עבודה קבוצתי",
        ),
        "migdal:a": DocumentMeta(
            company_id="migdal",
            domain="life",
            appendix_name="שלב",
            coverage_type="אובדן כושר עבודה",
        ),
    }

    assert find_cross_company_matches(embeddings, meta) == []


def test_falls_back_to_next_best_candidate_when_top_one_is_uncorroborated() -> None:
    """The new "skip an uncorroborated top candidate" behavior must not
    throw away a company's match entirely when a *different*, corroborated
    candidate from that same company is available - only the specific
    uncorroborated document is skipped, not the whole company."""
    embeddings = {
        "migdal:a": [1.0, 0.0],
        "phoenix:unrelated": [0.999, 0.0447],  # highest score, but no lexical overlap at all
        "phoenix:related": [0.9, 0.43588989],  # lower score, but shares a real content word
    }
    meta = {
        "migdal:a": DocumentMeta(
            company_id="migdal",
            domain="health",
            appendix_name="ניתוחים בישראל",
            coverage_type="ביטוח בריאות",
        ),
        "phoenix:unrelated": DocumentMeta(
            company_id="phoenix",
            domain="health",
            appendix_name="פירוט מסמכים נדרשים",
            coverage_type="ביטוח בריאות",
        ),
        "phoenix:related": DocumentMeta(
            company_id="phoenix",
            domain="health",
            appendix_name="גילוי נאות - ניתוחים בחו״ל",
            coverage_type="ביטוח בריאות",
        ),
    }

    matches = find_cross_company_matches(embeddings, meta)
    by_doc = {m.document_id: m for m in matches}
    assert by_doc["migdal:a"].matched_document_id == "phoenix:related"


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


def test_inactive_document_is_still_a_valid_source() -> None:
    """A superseded/historical appendix (is_active=False) must still be
    able to find its current cross-company equivalent - only being a
    CANDIDATE is restricted, not being a source. See DocumentMeta.is_active."""
    embeddings = {
        "migdal:old": [1.0, 0.0],
        "phoenix:current": [1.0, 0.0],
    }
    meta = {
        "migdal:old": DocumentMeta(company_id="migdal", domain="health", is_active=False),
        "phoenix:current": DocumentMeta(company_id="phoenix", domain="health", is_active=True),
    }

    matches = find_cross_company_matches(embeddings, meta)
    by_doc = {m.document_id: m for m in matches}
    assert by_doc["migdal:old"].matched_document_id == "phoenix:current"


def test_inactive_document_is_never_a_candidate() -> None:
    """An active document must never be matched *against* a superseded one,
    even if it's the closest embedding score available. With only these two
    documents: phoenix:old (source) legitimately finds migdal:current
    (active candidate) - that's the asymmetry this fix is for - but
    migdal:current (source) must find NO match at all, since its only
    possible candidate (phoenix:old) is inactive."""
    embeddings = {
        "migdal:current": [1.0, 0.0],
        "phoenix:old": [1.0, 0.0],
    }
    meta = {
        "migdal:current": DocumentMeta(company_id="migdal", domain="health", is_active=True),
        "phoenix:old": DocumentMeta(company_id="phoenix", domain="health", is_active=False),
    }

    matches = find_cross_company_matches(embeddings, meta)
    by_doc = {m.document_id: m for m in matches}
    assert "migdal:current" not in by_doc
    assert by_doc["phoenix:old"].matched_document_id == "migdal:current"


def test_inactive_vs_inactive_never_matches() -> None:
    embeddings = {
        "migdal:old": [1.0, 0.0],
        "phoenix:old": [1.0, 0.0],
    }
    meta = {
        "migdal:old": DocumentMeta(company_id="migdal", domain="health", is_active=False),
        "phoenix:old": DocumentMeta(company_id="phoenix", domain="health", is_active=False),
    }

    assert find_cross_company_matches(embeddings, meta) == []


def test_rank_candidates_by_company_excludes_inactive_candidates() -> None:
    """Same active-candidate-only rule applies to the judge shortlist path
    (core.matching.semantic_judge via scripts/judge_matches.py)."""
    embeddings = {
        "migdal:current": [1.0, 0.0],
        "phoenix:old": [1.0, 0.0],
        "phoenix:current": [0.99, 0.14106736],
    }
    meta = {
        "migdal:current": DocumentMeta(company_id="migdal", domain="health", is_active=True),
        "phoenix:old": DocumentMeta(company_id="phoenix", domain="health", is_active=False),
        "phoenix:current": DocumentMeta(company_id="phoenix", domain="health", is_active=True),
    }

    result = rank_candidates_by_company(embeddings, meta)
    candidate_ids = {cid for cid, _score in result["migdal:current"]["phoenix"]}
    assert candidate_ids == {"phoenix:current"}
