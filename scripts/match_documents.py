"""Compute cross-company document matches from existing embeddings.

For every embedded document, finds its best-matching document from another
company in the same domain (health/life), and records the pairing with a
status: auto_confirmed (similarity >= threshold) or pending_review
(below it, needs a human look via the dashboard).

Usage: python scripts/match_documents.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import Document, DocumentEmbedding, DocumentMatch  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.matching.similarity import DocumentMeta, find_cross_company_matches  # noqa: E402
from core.models.enums import MatchStatus  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_db()

    with session_scope() as session:
        embeddings_by_doc = {
            row.document_id: row.embedding for row in session.scalars(select(DocumentEmbedding))
        }
        doc_meta = {
            d.id: DocumentMeta(company_id=d.company_id, domain=d.domain)
            for d in session.scalars(select(Document))
        }

    if not embeddings_by_doc:
        logger.info("No embeddings yet - nothing to match.")
        return

    matches = find_cross_company_matches(embeddings_by_doc, doc_meta)
    logger.info("Computed %d candidate matches", len(matches))

    auto_confirmed = 0
    pending_review = 0
    for match in matches:
        row = DocumentMatch(
            id=f"{match.document_id}:{match.matched_document_id}",
            document_id=match.document_id,
            matched_document_id=match.matched_document_id,
            similarity_score=match.similarity_score,
            status=match.status.value,
        )
        with session_scope() as session:
            session.merge(row)
        if match.status == MatchStatus.AUTO_CONFIRMED:
            auto_confirmed += 1
        else:
            pending_review += 1

    logger.info("Done. auto_confirmed=%d pending_review=%d", auto_confirmed, pending_review)


if __name__ == "__main__":
    main()
