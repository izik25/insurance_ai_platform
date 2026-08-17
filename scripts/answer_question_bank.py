"""Phase 2: run the Question Bank (base + category-specific questions) over
every classified document.

For each `Document` that has a `DocumentClassification` but hasn't had its
question bank answered yet at the current question_bank_version (per
`document_pipeline_status`): reads the full document text (same
embedded-text/OCR-fallback path as scripts/extract_documents.py - question
answers need real page/evidence citations, unlike classification which
works off already-extracted fields), asks ONE Batch API call for the
document's entire applicable question set (base questions +
core/knowledge_base's category_questions for its category_id), and persists
FOUND/NOT_FOUND/NOT_APPLICABLE/AMBIGUOUS answers with evidence + additional
findings. Chunked + checkpointed like extract_documents.py.

Usage: python scripts/answer_question_bank.py [--limit N] [--chunk-size N]
       [--company migdal,phoenix] [--category-prefix health.critical_illness]
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

from core.config.settings import Settings, get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentAdditionalFinding,
    DocumentClassification,
    DocumentPipelineStatus,
    DocumentQuestionAnswer,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError, OcrError, PdfProcessingError  # noqa: E402
from core.extraction.text_extraction import get_document_text  # noqa: E402
from core.knowledge_base.registry import DEFAULT_VERSION as QUESTION_BANK_VERSION  # noqa: E402
from core.knowledge_base.registry import get_questions_for_category  # noqa: E402
from core.knowledge_base.schema import QuestionDef  # noqa: E402
from core.ocr.engine import TesseractEngine  # noqa: E402
from core.questions.llm_answer import (  # noqa: E402
    collect_question_results,
    submit_question_batch,
    wait_for_batch,
)
from core.questions.schema import QuestionAnswerBatch  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _pending_documents(
    session: Session, companies: list[str] | None, category_prefix: str | None
) -> list[tuple[str, str, str]]:
    """Returns (document_id, file_path, category_id) for documents classified
    but not yet answered at the current question_bank_version."""
    already_current = set(
        session.scalars(
            select(DocumentPipelineStatus.document_id).where(
                DocumentPipelineStatus.question_bank_version == QUESTION_BANK_VERSION
            )
        )
    )

    query = select(Document, DocumentClassification).join(
        DocumentClassification, DocumentClassification.document_id == Document.id
    )
    if companies:
        query = query.where(Document.company_id.in_(companies))

    pending = []
    for document, classification in session.execute(query):
        if document.id in already_current:
            continue
        if category_prefix and not classification.category_id.startswith(category_prefix):
            continue
        pending.append((document.id, document.file_path, classification.category_id))
    return pending


def _read_texts(
    chunk: list[tuple[str, str, str]], settings: Settings, ocr_engine: TesseractEngine
) -> dict[str, tuple[str, list[QuestionDef]]]:
    documents: dict[str, tuple[str, list[QuestionDef]]] = {}
    for index, (document_id, file_path, category_id) in enumerate(chunk, start=1):
        logger.debug("Reading %d/%d: %s", index, len(chunk), document_id)
        absolute_path = settings.raw_documents_dir / file_path
        try:
            text, _method = get_document_text(absolute_path, ocr_engine)
            if text.strip():
                questions = get_questions_for_category(category_id)
                documents[document_id] = (text, questions)
            else:
                logger.warning("No text extracted for %s; skipping", document_id)
        except (PdfProcessingError, OcrError) as exc:
            logger.warning("Failed to read %s: %s", document_id, exc)
        if index % 10 == 0:
            logger.info("Reading progress: %d/%d in this chunk", index, len(chunk))
    return documents


def _save_result(document_id: str, result: QuestionAnswerBatch, questions: list[QuestionDef]) -> None:
    scope_by_id = {}
    base_ids = {q.question_id for q in questions if q.question_id.startswith("base.")}
    for question in questions:
        scope_by_id[question.question_id] = "base" if question.question_id in base_ids else "category"

    with session_scope() as session:
        for answer in result.answers:
            row = DocumentQuestionAnswer(
                document_id=document_id,
                question_bank_version=QUESTION_BANK_VERSION,
                question_id=answer.question_id,
                question_scope=scope_by_id.get(answer.question_id, "category"),
                status=answer.status,
                answer_text=answer.answer_text,
                evidence_text=answer.evidence_text,
                evidence_page=answer.evidence_page,
                evidence_section=answer.evidence_section,
                raw_response=answer.model_dump(),
            )
            session.add(row)
        for finding in result.additional_findings:
            session.add(
                DocumentAdditionalFinding(
                    document_id=document_id,
                    finding_text=finding.finding_text,
                    related_field=finding.related_field,
                    evidence_page=finding.evidence_page,
                )
            )
        status = session.get(DocumentPipelineStatus, document_id)
        if status is None:
            status = DocumentPipelineStatus(document_id=document_id)
        status.questions_answered_at = datetime.now(UTC)
        status.question_bank_version = QUESTION_BANK_VERSION
        session.merge(status)


def _run_chunk(client: OpenAI, model: str, documents: dict[str, tuple[str, list[QuestionDef]]]) -> tuple[int, int]:
    batch_id = submit_question_batch(client, model, documents)
    wait_for_batch(client, batch_id)
    expected = {doc_id: [q.question_id for q in questions] for doc_id, (_text, questions) in documents.items()}
    results = collect_question_results(client, batch_id, expected)

    saved = 0
    failed = 0
    for document_id, result in results.items():
        if result is None:
            failed += 1
            continue
        _, questions = documents[document_id]
        # DocumentQuestionAnswer has a unique (document_id, question_bank_version,
        # question_id) index - delete any prior rows for this doc+version
        # first (idempotent re-run of a partially-saved chunk after a crash).
        with session_scope() as session:
            session.query(DocumentQuestionAnswer).filter_by(
                document_id=document_id, question_bank_version=QUESTION_BANK_VERSION
            ).delete()
        _save_result(document_id, result, questions)
        saved += 1
    return saved, failed


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--chunk-size", type=int, default=50)
    arg_parser.add_argument("--company", type=str, default=None, help="Comma-separated company_id list")
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not set")

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        pending = _pending_documents(session, companies, args.category_prefix)

    if args.limit is not None:
        pending = pending[: args.limit]

    if not pending:
        logger.info("Nothing to answer - all matching documents already processed.")
        return

    chunk_size = args.chunk_size
    chunks = [pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)]
    logger.info(
        "%d documents pending question-bank answering, split into %d chunks of up to %d.",
        len(pending),
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
        logger.info("Chunk %d/%d: reading text for %d documents...", chunk_index, len(chunks), len(chunk))
        documents = _read_texts(chunk, settings, ocr_engine)

        if not documents:
            logger.warning("Chunk %d/%d: no extractable text; skipping.", chunk_index, len(chunks))
            continue

        logger.info("Chunk %d/%d: submitting batch for %d documents...", chunk_index, len(chunks), len(documents))
        saved, failed = _run_chunk(client, settings.extraction_model, documents)
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
