"""Cross-company document matching by embedding cosine similarity.

Appendix numbers are company-specific (Migdal's נספח 105 and Phoenix's
נספח 105 are unrelated documents), so matching has to go by content. With
a corpus in the low thousands, a plain in-memory cosine-similarity matrix
(numpy) is trivially fast - no need for pgvector or an ANN index at this
scale.

Embedding similarity alone can be fooled by near-empty extractions (see
core.matching.lexical's module docstring: two documents the LLM found
almost nothing in both reduce to the same short generic embedding text and
"match" at a near-perfect score with no real relationship) and by group
policies scoped to one employer (see group_scope_signature's docstring).
Lexical corroboration (appendix_name/coverage_type/coverage_name sharing a
meaningful word, and compatible employer/group scope) therefore gates two
things, not just one:

- WHICH candidate is picked as a company's match at all: a company's
  highest-scoring document is only chosen if it's corroborated: an
  uncorroborated top candidate is skipped in favor of the next-best
  corroborated one, and if *no* candidate from a company is corroborated,
  that company contributes no match for this document at all - "not
  related at all" is not the same as "maybe related, needs a human look",
  and shouldn't occupy a slot in the pending-review queue (confirmed live:
  a corrupted-extraction Clal document scoring >=0.97 against dozens of
  totally unrelated life-insurance appendices from every other company was
  cluttering pending_review with obvious non-matches).
- Among corroborated candidates, similarity score alone then decides
  AUTO_CONFIRMED vs PENDING_REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.config.settings import get_settings
from core.matching.lexical import combined_tokens, group_scope_signature, has_lexical_overlap
from core.models.enums import MatchStatus


@dataclass(frozen=True)
class DocumentMeta:
    company_id: str
    domain: str
    appendix_name: str | None = None
    coverage_type: str | None = None
    coverage_name: str | None = None
    # Document.is_active (see core/database/models.py) - defaults to True so
    # every existing caller/test that doesn't pass this keeps behaving
    # exactly as before. Only gates CANDIDATE eligibility (see the `if not
    # other_meta.is_active: continue` checks below) - a document itself
    # never needs to be active to be matched *from*: a superseded/old
    # appendix should still be able to find its current cross-company
    # equivalent, it just must never be matched *against* another
    # superseded appendix (old-vs-old is never a meaningful comparison,
    # since neither side describes what's actually being sold today).
    is_active: bool = True


@dataclass(frozen=True)
class MatchCandidate:
    document_id: str
    matched_document_id: str
    similarity_score: float
    status: MatchStatus


def _lexically_corroborated(meta: DocumentMeta, other_meta: DocumentMeta) -> bool:
    """Do these two documents' descriptive fields share a meaningful word -
    and, if either is restricted to a specific employer/organization, is the
    other restricted to that *same* one?

    Skipped (treated as corroborated) when neither side has any of
    appendix_name/coverage_type/coverage_name available at all - there's
    nothing to compare, so this isn't evidence against the match (this also
    keeps callers that don't pass this metadata, e.g. existing tests,
    behaving exactly as before).

    The group-scope check runs first and can reject a pair the plain word
    overlap below would otherwise wave through: a document scoped to one
    employer (group_scope_signature returns non-None) must not corroborate
    against a document with no scoping at all, or scoped to a *different*
    employer - see group_scope_signature's docstring for the real case this
    was written for (a Bank Hapoalim-only group policy matching hundreds of
    unrelated general-population appendices on the word "אובדן כושר עבודה"
    alone).
    """
    scope = group_scope_signature(meta.appendix_name, meta.coverage_name)
    other_scope = group_scope_signature(other_meta.appendix_name, other_meta.coverage_name)
    if (scope is None) != (other_scope is None):
        return False
    if scope is not None and other_scope is not None and not has_lexical_overlap(
        scope, other_scope
    ):
        return False

    has_metadata = bool(meta.appendix_name or meta.coverage_type or meta.coverage_name) and bool(
        other_meta.appendix_name or other_meta.coverage_type or other_meta.coverage_name
    )
    if not has_metadata:
        return True

    bag = combined_tokens(meta.appendix_name, meta.coverage_type, meta.coverage_name)
    other_bag = combined_tokens(
        other_meta.appendix_name, other_meta.coverage_type, other_meta.coverage_name
    )
    return has_lexical_overlap(bag, other_bag)


def rank_candidates_by_company(
    embeddings_by_doc: dict[str, list[float]],
    doc_meta: dict[str, DocumentMeta],
    top_k: int = 3,
    min_score: float = 0.75,
) -> dict[str, dict[str, list[tuple[str, float]]]]:
    """For each document, the top-`top_k` embedding candidates from *each*
    other company in the same domain, scoring >= `min_score`.

    Feeds `core.matching.semantic_judge`: unlike `find_cross_company_matches`,
    this does no lexical corroboration at all - it's pure embedding recall,
    ranked, so the judge gets a shortlist to read rather than a single
    pre-decided candidate. `top_k` > 1 matters because the *embedding*
    top-1 for a company is occasionally not the true match (paraphrasing
    noise); giving the judge the next couple of candidates lets it pick the
    right one instead of being stuck with a bad #1.

    Returns document_id -> company_id -> [(candidate_id, score), ...],
    sorted by score descending.
    """
    doc_ids = [d for d in embeddings_by_doc if d in doc_meta]
    if len(doc_ids) < 2:
        return {}

    matrix = np.array([embeddings_by_doc[d] for d in doc_ids])
    similarity = matrix @ matrix.T

    result: dict[str, dict[str, list[tuple[str, float]]]] = {}
    for i, document_id in enumerate(doc_ids):
        meta = doc_meta[document_id]
        candidates_by_company: dict[str, list[tuple[str, float]]] = {}
        for j, other_id in enumerate(doc_ids):
            if i == j:
                continue
            other_meta = doc_meta[other_id]
            if other_meta.company_id == meta.company_id or other_meta.domain != meta.domain:
                continue
            if not other_meta.is_active:
                continue
            score = float(similarity[i, j])
            if score < min_score:
                continue
            candidates_by_company.setdefault(other_meta.company_id, []).append((other_id, score))

        doc_result = {}
        for company_id, candidates in candidates_by_company.items():
            candidates.sort(key=lambda pair: pair[1], reverse=True)
            doc_result[company_id] = candidates[:top_k]
        if doc_result:
            result[document_id] = doc_result

    return result


def find_cross_company_matches(
    embeddings_by_doc: dict[str, list[float]],
    doc_meta: dict[str, DocumentMeta],
) -> list[MatchCandidate]:
    """For each document, find its best *corroborated* match from each other company.

    Restricted to the same `domain` (health/life) and a different
    `company_id` - comparing a health appendix to a life appendix, or a
    company to itself, is never a meaningful match. A document can end up
    with one match per other company present in its domain (e.g. up to 5
    rows for a Migdal appendix matched against Phoenix, Clal, Menorah,
    DirectInsurance and Hachshara) - picking a single globally-best match
    across all companies would silently hide a good match with company B
    just because company A's match happened to score slightly higher.
    Embeddings are assumed pre-normalized (see
    `core.embeddings.model.embed_texts`), so cosine similarity is a plain
    dot product.

    Within one company, candidates are tried highest-scoring first, and the
    first one that passes `_lexically_corroborated` wins; a candidate that
    fails it is skipped in favor of the next-best one rather than being
    recorded as a low-confidence match, and if none of a company's
    candidates are corroborated, that company contributes nothing for this
    document at all.

    Every document in `doc_meta` is eligible as a SOURCE (the `document_id`
    side) regardless of `DocumentMeta.is_active` - a superseded/historical
    appendix still deserves to know its current cross-company equivalent.
    Only active documents are eligible as a CANDIDATE (the
    `matched_document_id` side): old-vs-old is never a meaningful
    comparison, and an inactive document must never anchor a match either
    (see DocumentMeta.is_active's docstring).
    """
    threshold = get_settings().similarity_auto_confirm_threshold
    doc_ids = [d for d in embeddings_by_doc if d in doc_meta]
    if len(doc_ids) < 2:
        return []

    matrix = np.array([embeddings_by_doc[d] for d in doc_ids])
    similarity = matrix @ matrix.T

    matches: list[MatchCandidate] = []
    for i, document_id in enumerate(doc_ids):
        meta = doc_meta[document_id]
        candidates_by_company: dict[str, list[tuple[int, float]]] = {}
        for j, other_id in enumerate(doc_ids):
            if i == j:
                continue
            other_meta = doc_meta[other_id]
            if other_meta.company_id == meta.company_id or other_meta.domain != meta.domain:
                continue
            if not other_meta.is_active:
                continue
            score = float(similarity[i, j])
            candidates_by_company.setdefault(other_meta.company_id, []).append((j, score))

        for candidates in candidates_by_company.values():
            candidates.sort(key=lambda pair: pair[1], reverse=True)
            for candidate_index, candidate_score in candidates:
                other_meta = doc_meta[doc_ids[candidate_index]]
                if not _lexically_corroborated(meta, other_meta):
                    continue
                status = (
                    MatchStatus.AUTO_CONFIRMED
                    if candidate_score >= threshold
                    else MatchStatus.PENDING_REVIEW
                )
                matches.append(
                    MatchCandidate(
                        document_id=document_id,
                        matched_document_id=doc_ids[candidate_index],
                        similarity_score=candidate_score,
                        status=status,
                    )
                )
                break

    return matches
