"""Extract structured fields from every processed document via OpenAI.

For each `Document` row without a `DocumentExtraction` yet: reads the PDF's
full text (embedded text, OCR fallback for scanned pages), submits it to
OpenAI's Batch API for structured-field extraction, and persists the
result. Company-agnostic - works on any document from any company, no
per-company logic. Idempotent: already-extracted documents are skipped, so
re-running only processes documents added since the last run.

Processes documents in chunks (`--chunk-size`, default 100): each chunk is
read, submitted as its own batch, and saved to the DB before the next chunk
starts. This is the checkpoint - a crash or interruption loses at most the
in-flight chunk, not the whole run. Simply re-run the same command to
resume; already-extracted documents are skipped automatically.

Usage: python scripts/extract_documents.py [--limit N] [--chunk-size N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.config.settings import Settings, get_settings  # noqa: E402
from core.database.models import Document, DocumentExtraction  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError, OcrError, PdfProcessingError  # noqa: E402
from core.extraction.llm_extract import (  # noqa: E402
    collect_extraction_results,
    submit_extraction_batch,
    wait_for_batch,
)
from core.extraction.schema import PolicyExtraction  # noqa: E402
from core.extraction.text_extraction import get_document_text  # noqa: E402
from core.ocr.engine import TesseractEngine  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _read_texts(
    chunk: list[tuple[str, str]], settings: Settings, ocr_engine: TesseractEngine
) -> dict[str, str]:
    texts: dict[str, str] = {}
    for index, (document_id, file_path) in enumerate(chunk, start=1):
        logger.debug("Reading %d/%d: %s", index, len(chunk), document_id)
        absolute_path = settings.raw_documents_dir / file_path
        try:
            text, _method = get_document_text(absolute_path, ocr_engine)
            if text.strip():
                texts[document_id] = text
            else:
                logger.warning("No text extracted for %s; skipping", document_id)
        except (PdfProcessingError, OcrError) as exc:
            logger.warning("Failed to read %s: %s", document_id, exc)
        if index % 10 == 0:
            logger.info("Reading progress: %d/%d in this chunk", index, len(chunk))
    return texts


def _backfill_appendix_metadata(
    session: Session, document_id: str, extraction: PolicyExtraction
) -> None:
    """Fill in Document.appendix_number/appendix_name from the LLM's reading,
    but only where the trusted source metadata left them empty (Migdal has no
    site-provided appendix numbers for ~half its documents; Phoenix already
    has 100% coverage from its own metadata and should never be overwritten -
    see PROJECT_OVERVIEW.md's "trust the source, don't guess" principle)."""
    if not extraction.appendix_number and not extraction.appendix_name:
        return
    document = session.get(Document, document_id)
    if document is None:
        return
    if not document.appendix_number and extraction.appendix_number:
        document.appendix_number = extraction.appendix_number
    if not document.appendix_name and extraction.appendix_name:
        document.appendix_name = extraction.appendix_name


def _run_chunk(client: OpenAI, model: str, texts: dict[str, str]) -> tuple[int, int]:
    """Submit one batch, wait for it, and save results. Returns (saved, failed)."""
    batch_id = submit_extraction_batch(client, model, texts)
    wait_for_batch(client, batch_id)
    results = collect_extraction_results(client, batch_id)

    saved = 0
    failed = 0
    for document_id, extraction in results.items():
        if extraction is None:
            failed += 1
            continue
        row = DocumentExtraction(
            document_id=document_id,
            coverage_type=extraction.coverage_type,
            coverage_name=extraction.coverage_name,
            eligibility_conditions=extraction.eligibility_conditions,
            insurance_amounts=extraction.insurance_amounts,
            qualifying_period=extraction.qualifying_period,
            waiting_period=extraction.waiting_period,
            exclusions=extraction.exclusions,
            age_range=extraction.age_range,
            restrictions=extraction.restrictions,
            tables={"tables": [t.model_dump() for t in extraction.tables]},
            disease_count=extraction.disease_count,
            disease_list=extraction.disease_list,
            survival_period=extraction.survival_period,
            raw_extraction=extraction.model_dump(),
        )
        try:
            with session_scope() as session:
                session.merge(row)
                _backfill_appendix_metadata(session, document_id, extraction)
        except IntegrityError as exc:
            # A batch has occasionally been observed to yield two results for
            # the same document_id (root cause not pinned down - possibly a
            # retried request within the batch); document_id is unique, so
            # the second merge collides. Don't let one bad row abort the rest
            # of an otherwise-good 100-document chunk.
            logger.warning("Skipping duplicate extraction result for %s: %s", document_id, exc)
            failed += 1
            continue
        saved += 1

    return saved, failed


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--chunk-size", type=int, default=100)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not set")

    with session_scope() as session:
        already_extracted = set(session.scalars(select(DocumentExtraction.document_id)))
        pending_info = [
            (d.id, d.file_path)
            for d in session.scalars(select(Document))
            if d.id not in already_extracted
        ]

    if args.limit is not None:
        pending_info = pending_info[: args.limit]

    if not pending_info:
        logger.info("Nothing to extract - all documents already processed.")
        return

    chunk_size = args.chunk_size
    chunks = [pending_info[i : i + chunk_size] for i in range(0, len(pending_info), chunk_size)]
    logger.info(
        "%d documents pending, split into %d chunks of up to %d.",
        len(pending_info),
        len(chunks),
        chunk_size,
    )

    ocr_engine = TesseractEngine(
        settings.tessdata_dir, lang=settings.ocr_language, timeout_seconds=settings.ocr_timeout_seconds
    )
    client = OpenAI(api_key=settings.openai_api_key)

    total_saved = 0
    total_failed = 0
    for chunk_index, chunk in enumerate(chunks, start=1):
        logger.info(
            "Chunk %d/%d: reading text for %d documents...",
            chunk_index,
            len(chunks),
            len(chunk),
        )
        texts = _read_texts(chunk, settings, ocr_engine)

        if not texts:
            logger.warning("Chunk %d/%d: no extractable text; skipping.", chunk_index, len(chunks))
            continue

        logger.info("Chunk %d/%d: submitting batch for %d documents...", chunk_index, len(chunks), len(texts))
        saved, failed = _run_chunk(client, settings.extraction_model, texts)
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
