"""Cross-company document matching by embedding cosine similarity.

Appendix numbers are company-specific (Migdal's נספח 105 and Phoenix's
נספח 105 are unrelated documents), so matching has to go by content. With
a corpus in the low thousands, a plain in-memory cosine-similarity matrix
(numpy) is trivially fast - no need for pgvector or an ANN index at this
scale.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.config.settings import get_settings
from core.models.enums import MatchStatus


@dataclass(frozen=True)
class DocumentMeta:
    company_id: str
    domain: str


@dataclass(frozen=True)
class MatchCandidate:
    document_id: str
    matched_document_id: str
    similarity_score: float
    status: MatchStatus


def find_cross_company_matches(
    embeddings_by_doc: dict[str, list[float]],
    doc_meta: dict[str, DocumentMeta],
) -> list[MatchCandidate]:
    """For each document, find its best-matching document from another company.

    Restricted to the same `domain` (health/life) and a different
    `company_id` - comparing a health appendix to a life appendix, or a
    company to itself, is never a meaningful match. Embeddings are assumed
    pre-normalized (see `core.embeddings.model.embed_texts`), so cosine
    similarity is a plain dot product.
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
        best_index: int | None = None
        best_score = -1.0
        for j, other_id in enumerate(doc_ids):
            if i == j:
                continue
            other_meta = doc_meta[other_id]
            if other_meta.company_id == meta.company_id or other_meta.domain != meta.domain:
                continue
            score = float(similarity[i, j])
            if score > best_score:
                best_score = score
                best_index = j

        if best_index is None:
            continue

        status = (
            MatchStatus.AUTO_CONFIRMED
            if best_score >= threshold
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
