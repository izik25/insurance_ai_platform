"""Extract structured fields from every processed document via Claude.

For each `Document` row without a `DocumentExtraction` yet: reads the PDF's
full text (embedded text, OCR fallback for scanned pages), submits it to
Claude's Message Batches API for structured-field extraction, and persists
the result. Company-agnostic - works on any document from any company, no
per-company logic. Idempotent: already-extracted documents are skipped, so
re-running only processes documents added since the last run.

Usage: python scripts/extract_documents.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import Anthropic  # noqa: E402
from sqlalchemy import select  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import Document, DocumentExtraction  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError, OcrError, PdfProcessingError  # noqa: E402
from core.extraction.llm_extract import (  # noqa: E402
    collect_extraction_results,
    submit_extraction_batch,
    wait_for_batch,
)
from core.extraction.text_extraction import get_document_text  # noqa: E402
from core.ocr.engine import TesseractEngine  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    if not settings.anthropic_api_key:
        raise ConfigurationError("ANTHROPIC_API_KEY is not set")

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

    logger.info("Reading text for %d documents...", len(pending_info))
    ocr_engine = TesseractEngine(settings.tessdata_dir, lang=settings.ocr_language)

    texts: dict[str, str] = {}
    for index, (document_id, file_path) in enumerate(pending_info, start=1):
        absolute_path = settings.raw_documents_dir / file_path
        try:
            text, _method = get_document_text(absolute_path, ocr_engine)
            if text.strip():
                texts[document_id] = text
            else:
                logger.warning("No text extracted for %s; skipping", document_id)
        except (PdfProcessingError, OcrError) as exc:
            logger.warning("Failed to read %s: %s", document_id, exc)

        if index % 50 == 0:
            logger.info("Text extraction progress: %d/%d", index, len(pending_info))

    if not texts:
        logger.warning("No documents had extractable text - nothing to submit.")
        return

    client = Anthropic(api_key=settings.anthropic_api_key)
    batch_id = submit_extraction_batch(client, settings.extraction_model, texts)
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
        with session_scope() as session:
            session.merge(row)
        saved += 1

    logger.info("Done. saved=%d failed=%d total_submitted=%d", saved, failed, len(texts))


if __name__ == "__main__":
    main()
