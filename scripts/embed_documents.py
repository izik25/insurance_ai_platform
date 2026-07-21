"""Compute embeddings for every document that has an extraction but no embedding.

The embedding input is the extracted fields (coverage type/name, eligibility,
exclusions, disease list, etc.), not the raw document text - see
`PolicyExtraction.embedding_text()` - so cross-company matching isn't thrown
off by two insurers phrasing the same coverage differently.

Processes documents in chunks (`--chunk-size`, default 200) and saves each
chunk before moving to the next, so an interruption loses at most the
in-flight chunk. Re-run the same command to resume.

Usage: python scripts/embed_documents.py [--limit N] [--chunk-size N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import DocumentEmbedding, DocumentExtraction  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.embeddings.model import embed_texts  # noqa: E402
from core.extraction.schema import PolicyExtraction  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _to_policy_extraction(row: DocumentExtraction) -> PolicyExtraction:
    return PolicyExtraction(
        coverage_type=row.coverage_type,
        coverage_name=row.coverage_name,
        eligibility_conditions=row.eligibility_conditions,
        insurance_amounts=row.insurance_amounts,
        qualifying_period=row.qualifying_period,
        waiting_period=row.waiting_period,
        exclusions=row.exclusions,
        age_range=row.age_range,
        restrictions=row.restrictions,
        disease_count=row.disease_count,
        disease_list=row.disease_list,
        survival_period=row.survival_period,
    )


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--chunk-size", type=int, default=200)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    with session_scope() as session:
        already_embedded = set(session.scalars(select(DocumentEmbedding.document_id)))
        pending = [
            (row.document_id, _to_policy_extraction(row).embedding_text())
            for row in session.scalars(select(DocumentExtraction))
            if row.document_id not in already_embedded
        ]

    if args.limit is not None:
        pending = pending[: args.limit]
    pending = [(document_id, text) for document_id, text in pending if text.strip()]

    if not pending:
        logger.info("Nothing to embed - all extracted documents already have embeddings.")
        return

    chunk_size = args.chunk_size
    chunks = [pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)]
    logger.info(
        "%d documents pending, split into %d chunks of up to %d.",
        len(pending),
        len(chunks),
        chunk_size,
    )

    model_name = settings.embedding_model_name
    total_embedded = 0
    for chunk_index, chunk in enumerate(chunks, start=1):
        logger.info("Chunk %d/%d: embedding %d documents...", chunk_index, len(chunks), len(chunk))
        document_ids = [document_id for document_id, _ in chunk]
        vectors = embed_texts([text for _, text in chunk])

        for document_id, vector in zip(document_ids, vectors, strict=True):
            with session_scope() as session:
                session.merge(
                    DocumentEmbedding(
                        document_id=document_id, embedding=vector, model_name=model_name
                    )
                )

        total_embedded += len(chunk)
        logger.info(
            "Chunk %d/%d done. (running total: embedded=%d)",
            chunk_index,
            len(chunks),
            total_embedded,
        )

    logger.info("Done. embedded=%d", total_embedded)


if __name__ == "__main__":
    main()
