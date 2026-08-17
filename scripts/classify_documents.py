"""Phase 2: classify every extracted document into a taxonomy category_id
(core/taxonomy/data/taxonomy.v*.yaml).

For each `Document` with a `DocumentExtraction` but not yet classified at
the current taxonomy_version (per `document_pipeline_status`): builds a
compact input from the already-extracted structured fields (no re-reading
of the PDF/OCR - see core/classification/llm_classify.build_classification_input),
submits it to OpenAI's Batch API with the category_id constrained to the
taxonomy's known set, and persists the result. Chunked + checkpointed like
scripts/extract_documents.py: each chunk is its own batch, saved before the
next chunk starts.

Idempotent per taxonomy_version: bumping core/taxonomy/data/taxonomy.v2.yaml
and pointing DEFAULT_VERSION at it would re-classify everything; re-running
with the same version only processes newly-extracted documents.

Usage: python scripts/classify_documents.py [--limit N] [--chunk-size N]
       [--company migdal,phoenix] [--domain health]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.classification.llm_classify import (  # noqa: E402
    build_classification_input,
    collect_classification_results,
    submit_classification_batch,
    wait_for_batch,
)
from core.classification.schema import ClassificationResult  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentClassification,
    DocumentExtraction,
    DocumentPipelineStatus,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError  # noqa: E402
from core.taxonomy.registry import DEFAULT_VERSION  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _pending_documents(
    session: Session, taxonomy_version: str, companies: list[str] | None, domain: str | None
) -> list[tuple[str, DocumentExtraction, str | None, str | None]]:
    already_current = set(
        session.scalars(
            select(DocumentPipelineStatus.document_id).where(
                DocumentPipelineStatus.taxonomy_version == taxonomy_version
            )
        )
    )

    query = select(Document, DocumentExtraction).join(
        DocumentExtraction, DocumentExtraction.document_id == Document.id
    )
    if companies:
        query = query.where(Document.company_id.in_(companies))
    if domain:
        query = query.where(Document.domain == domain)

    pending = []
    for document, extraction in session.execute(query):
        if document.id in already_current:
            continue
        pending.append((document.id, extraction, document.department_name, document.appendix_name))
    return pending


def _run_chunk(
    client: OpenAI, model: str, chunk: list[tuple[str, DocumentExtraction, str | None, str | None]]
) -> tuple[int, int]:
    documents = {
        document_id: build_classification_input(extraction, department_name, appendix_name)
        for document_id, extraction, department_name, appendix_name in chunk
    }
    batch_id = submit_classification_batch(client, model, documents)
    wait_for_batch(client, batch_id)
    results = collect_classification_results(client, batch_id)

    saved = 0
    failed = 0
    for document_id, result in results.items():
        if result is None:
            failed += 1
            continue
        _save_result(document_id, result)
        saved += 1
    return saved, failed


def _save_result(document_id: str, result: ClassificationResult) -> None:
    from core.taxonomy.registry import get_category

    category = get_category(result.category_id)
    row = DocumentClassification(
        document_id=document_id,
        taxonomy_version=DEFAULT_VERSION,
        category_id=result.category_id,
        main_category=category.main_category,
        coverage_family=category.coverage_family,
        coverage_subtype=category.coverage_subtype,
        coverage_variant=category.coverage_variant,
        benefit_model=category.benefit_model,
        target_population=category.target_population,
        alternative_categories=result.alternative_category_ids,
        confidence=result.confidence,
        evidence=result.evidence,
        raw_response=result.model_dump(),
    )
    with session_scope() as session:
        session.merge(row)
        status = session.get(DocumentPipelineStatus, document_id)
        if status is None:
            status = DocumentPipelineStatus(document_id=document_id)
        status.classified_at = datetime.now(UTC)
        status.taxonomy_version = DEFAULT_VERSION
        session.merge(status)


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--chunk-size", type=int, default=100)
    arg_parser.add_argument("--company", type=str, default=None, help="Comma-separated company_id list")
    arg_parser.add_argument("--domain", type=str, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not set")

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        pending = _pending_documents(session, DEFAULT_VERSION, companies, args.domain)

    if args.limit is not None:
        pending = pending[: args.limit]

    if not pending:
        logger.info("Nothing to classify - all matching documents already classified.")
        return

    chunk_size = args.chunk_size
    chunks = [pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)]
    logger.info(
        "%d documents pending classification, split into %d chunks of up to %d.",
        len(pending),
        len(chunks),
        chunk_size,
    )

    client = OpenAI(api_key=settings.openai_api_key)

    total_saved = 0
    total_failed = 0
    for chunk_index, chunk in enumerate(chunks, start=1):
        logger.info("Chunk %d/%d: submitting batch for %d documents...", chunk_index, len(chunks), len(chunk))
        saved, failed = _run_chunk(client, settings.extraction_model, chunk)
        total_saved += saved
        total_failed += failed
        logger.info(
            "Chunk %d/%d done. saved=%d failed=%d (running total: saved=%d failed=%d)",
            chunk_index,
            len(chunks),
            saved,
            failed,
            total_saved,
            total_failed,
        )

    logger.info("All chunks done. total_saved=%d total_failed=%d", total_saved, total_failed)


if __name__ == "__main__":
    main()
