"""Compute cross-company document matches from existing embeddings.

For every embedded document, finds its best-matching document from *each*
other company in the same domain (health/life), and records each pairing
with a status: auto_confirmed (similarity >= threshold AND the two
documents' appendix_name/coverage_type/coverage_name share a meaningful
word - see core.matching.similarity._lexically_corroborated) or
pending_review otherwise, needing a human look via the dashboard.

Every document is eligible as a SOURCE, but only currently-active documents
(Document.is_active - no marketing_end_date, or one that hasn't passed yet;
see companies/harel/downloader.py's module docstring for where that signal
comes from) are eligible as a CANDIDATE (the matched-against side): a
superseded historical appendix version is kept in the DB/dashboard for
viewing and download, and still deserves to be matched against whatever
currently-active appendix replaced it elsewhere, but it must never itself
anchor another old document's match, since old-vs-old never describes what
either side is actually selling today (enforced in
core.matching.similarity via DocumentMeta.is_active, not by pre-filtering
who's even in doc_meta). Documents from companies that don't publish a
marketing end date at all are always active (Document.is_active defaults
to True when the field is NULL).

Re-running this script recomputes the full match set from scratch and
replaces all previously auto-generated rows (auto_confirmed/pending_review)
- it never touches rows a human has already reviewed via the dashboard
(confirmed/rejected), so manual review decisions survive re-runs.

Usage: python scripts/match_documents.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentEmbedding,
    DocumentExtraction,
    DocumentMatch,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.matching.similarity import DocumentMeta, find_cross_company_matches  # noqa: E402
from core.models.enums import MatchStatus  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

_HUMAN_REVIEWED_STATUSES = (MatchStatus.CONFIRMED.value, MatchStatus.REJECTED.value)


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_db()

    with session_scope() as session:
        embeddings_by_doc = {
            row.document_id: row.embedding for row in session.scalars(select(DocumentEmbedding))
        }
        extractions_by_doc = {
            row.document_id: row for row in session.scalars(select(DocumentExtraction))
        }
        all_documents = session.scalars(select(Document)).all()
        inactive_count = sum(1 for d in all_documents if not d.is_active)
        doc_meta = {
            d.id: DocumentMeta(
                company_id=d.company_id,
                domain=d.domain,
                appendix_name=d.appendix_name,
                coverage_type=(
                    extractions_by_doc[d.id].coverage_type if d.id in extractions_by_doc else None
                ),
                coverage_name=(
                    extractions_by_doc[d.id].coverage_name if d.id in extractions_by_doc else None
                ),
                is_active=d.is_active,
            )
            for d in all_documents
        }
        protected_ids = set(
            session.scalars(
                select(DocumentMatch.id).where(DocumentMatch.status.in_(_HUMAN_REVIEWED_STATUSES))
            )
        )

    logger.info(
        "%d documents total, %d of them inactive/superseded (still eligible as a match SOURCE, "
        "never as a candidate - see module docstring).",
        len(all_documents),
        inactive_count,
    )

    if not embeddings_by_doc:
        logger.info("No embeddings yet - nothing to match.")
        return

    matches = find_cross_company_matches(embeddings_by_doc, doc_meta)
    logger.info("Computed %d candidate matches", len(matches))

    with session_scope() as session:
        session.execute(
            delete(DocumentMatch).where(DocumentMatch.status.notin_(_HUMAN_REVIEWED_STATUSES))
        )

    auto_confirmed = 0
    pending_review = 0
    skipped_protected = 0
    for match in matches:
        match_id = f"{match.document_id}:{match.matched_document_id}"
        if match_id in protected_ids:
            skipped_protected += 1
            continue
        row = DocumentMatch(
            id=match_id,
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

    logger.info(
        "Done. auto_confirmed=%d pending_review=%d skipped_protected=%d",
        auto_confirmed,
        pending_review,
        skipped_protected,
    )


if __name__ == "__main__":
    main()
