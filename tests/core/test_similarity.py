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
