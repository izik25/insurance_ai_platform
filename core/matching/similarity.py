"""Cross-company document matching by embedding cosine similarity.

Appendix numbers are company-specific (Migdal's נספח 105 and Phoenix's
נספח 105 are unrelated documents), so matching has to go by content. With
a corpus in the low thousands, a plain in-memory cosine-similarity matrix
(numpy) is trivially fast - no need for pgvector or an ANN index at this
scale.

Embedding similarity alone can be fooled by near-empty extractions (see
core.matching.lexical's module docstring: two documents the LLM found
almost nothing in both reduce to the same short generic embedding text and
"match" at a near-perfect score with no real relationship). AUTO_CONFIRMED
status therefore additionally requires the two documents' appendix_name/
coverage_type/coverage_name to share a meaningful word wherever that
metadata is actually available - confirmed live against the real corpus
(59 of 65 matches scoring >=0.999 were exactly this empty-extraction
pattern). This never changes WHICH document is selected as a company's
best match, only whether that match starts out auto-confirmed or waits
for a human to confirm it via the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.config.settings import get_settings
from core.matching.lexical import combined_tokens, has_lexical_overlap
from core.models.enums import MatchStatus


@dataclass(frozen=True)
class DocumentMeta:
    company_id: str
    domain: str
    appendix_name: str | None = None
    coverage_type: str | None = None
    coverage_name: str | None = None


@dataclass(frozen=True)
class MatchCandidate:
    document_id: str
    matched_document_id: str
    similarity_score: float
    status: MatchStatus


def _lexically_corroborated(meta: DocumentMeta, other_meta: DocumentMeta) -> bool:
    """Do these two documents' descriptive fields share a meaningful word?

    Skipped (treated as corroborated) when neither side has any of
    appendix_name/coverage_type/coverage_name available at all - there's
    nothing to compare, so this isn't evidence against the match (this also
    keeps callers that don't pass this metadata, e.g. existing tests,
    behaving exactly as before).
    """
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


def find_cross_company_matches(
    embeddings_by_doc: dict[str, list[float]],
    doc_meta: dict[str, DocumentMeta],
) -> list[MatchCandidate]:
    """For each document, find its best-matching document from *each* other company.

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
        best_by_company: dict[str, tuple[int, float]] = {}
        for j, other_id in enumerate(doc_ids):
            if i == j:
                continue
            other_meta = doc_meta[other_id]
            if other_meta.company_id == meta.company_id or other_meta.domain != meta.domain:
                continue
            score = float(similarity[i, j])
            current = best_by_company.get(other_meta.company_id)
            if current is None or score > current[1]:
                best_by_company[other_meta.company_id] = (j, score)

        for best_index, best_score in best_by_company.values():
            other_meta = doc_meta[doc_ids[best_index]]
            status = (
                MatchStatus.AUTO_CONFIRMED
                if best_score >= threshold and _lexically_corroborated(meta, other_meta)
                else MatchStatus.PENDING_REVIEW
            )
            matches.append(
                MatchCandidate(
                    document_id=document_id,
                    matched_document_id=doc_ids[best_index],
                    similarity_score=best_score,
                    status=status,
                )
            )

    return matches
