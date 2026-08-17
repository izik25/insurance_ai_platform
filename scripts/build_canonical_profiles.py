"""Phase 2: build the Canonical Coverage Profile for every document that has
finished question-bank answering.

DB-only input (DocumentExtraction + this document's DocumentQuestionAnswer/
DocumentAdditionalFinding rows) - no PDF/OCR re-read, see
core/canonical/profile_builder.py. Chunked + checkpointed like the other
Phase 2 scripts.

Usage: python scripts/build_canonical_profiles.py [--limit N] [--chunk-size N]
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

from core.canonical.profile_builder import (  # noqa: E402
    build_profile_input,
    collect_profile_results,
    submit_profile_batch,
    wait_for_batch,
)
from core.canonical.schema import PROFILE_VERSION, CanonicalCoverageProfile  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentAdditionalFinding,
    DocumentCanonicalProfile,
    DocumentClassification,
    DocumentExtraction,
    DocumentPipelineStatus,
    DocumentQuestionAnswer,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _pending_documents(
    session: Session, companies: list[str] | None, category_prefix: str | None
) -> list[str]:
    already_current = set(
        session.scalars(
            select(DocumentPipelineStatus.document_id).where(
                DocumentPipelineStatus.profile_version == PROFILE_VERSION
            )
        )
    )
    answered = set(
        session.scalars(
            select(DocumentPipelineStatus.document_id).where(
                DocumentPipelineStatus.questions_answered_at.is_not(None)
            )
        )
    )

    query = select(Document.id, DocumentClassification.category_id).join(
        DocumentClassification, DocumentClassification.document_id == Document.id
    )
    if companies:
        query = query.where(Document.company_id.in_(companies))

    pending = []
    for document_id, category_id in session.execute(query):
        if document_id in already_current or document_id not in answered:
            continue
        if category_prefix and not category_id.startswith(category_prefix):
            continue
        pending.append(document_id)
    return pending


def _build_inputs(session: Session, document_ids: list[str]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for document_id in document_ids:
        document = session.get(Document, document_id)
        extraction = session.scalar(
            select(DocumentExtraction).where(DocumentExtraction.document_id == document_id)
        )
        if document is None or extraction is None:
            continue
        answers = list(
            session.scalars(
                select(DocumentQuestionAnswer).where(DocumentQuestionAnswer.document_id == document_id)
            )
        )
        findings = list(
            session.scalars(
                select(DocumentAdditionalFinding).where(DocumentAdditionalFinding.document_id == document_id)
            )
        )
        inputs[document_id] = build_profile_input(extraction, document, answers, findings)
    return inputs


def _save_result(document_id: str, profile: CanonicalCoverageProfile) -> None:
    row = DocumentCanonicalProfile(
        document_id=document_id,
        profile_version=PROFILE_VERSION,
        insured_event=profile.insured_event,
        covered_events=profile.covered_events,
        covered_conditions=profile.covered_conditions,
        exclusions_normalized=profile.exclusions_normalized,
        limitations=profile.limitations,
        eligibility_normalized=profile.eligibility_normalized,
        waiting_period_days=None,  # computed deterministically in Phase 3 (core/fingerprint/parsers.py)
        qualifying_period_days=None,
        survival_period_days=None,
        benefit_type=profile.benefit_type,
        benefit_calculation=profile.benefit_calculation,
        amounts=profile.amounts,
        caps=profile.caps,
        deductible=profile.deductible.model_dump(),
        age_restrictions=profile.age_restrictions.model_dump(),
        pre_existing_condition_rules=profile.pre_existing_condition_rules,
        claim_requirements=profile.claim_requirements,
        definitions={d.term: d.definition for d in profile.definitions},
        extensions=profile.extensions,
        special_conditions=profile.special_conditions,
        termination_rules=profile.termination_rules,
        additional_findings_summary=profile.additional_findings_summary,
        raw_profile=profile.model_dump(),
    )
    with session_scope() as session:
        session.merge(row)
        status = session.get(DocumentPipelineStatus, document_id)
        if status is None:
            status = DocumentPipelineStatus(document_id=document_id)
        status.canonical_profile_at = datetime.now(UTC)
        status.profile_version = PROFILE_VERSION
        session.merge(status)


def _run_chunk(client: OpenAI, model: str, inputs: dict[str, str]) -> tuple[int, int]:
    batch_id = submit_profile_batch(client, model, inputs)
    wait_for_batch(client, batch_id)
    results = collect_profile_results(client, batch_id)

    saved = 0
    failed = 0
    for document_id, profile in results.items():
        if profile is None:
            failed += 1
            continue
        _save_result(document_id, profile)
        saved += 1
    return saved, failed


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--chunk-size", type=int, default=100)
    arg_parser.add_argument("--company", type=str, default=None)
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not set")

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        pending_ids = _pending_documents(session, companies, args.category_prefix)
        if args.limit is not None:
            pending_ids = pending_ids[: args.limit]
        if not pending_ids:
            logger.info("Nothing to build - all matching documents already have a canonical profile.")
            return
        chunk_size = args.chunk_size
        id_chunks = [pending_ids[i : i + chunk_size] for i in range(0, len(pending_ids), chunk_size)]
        input_chunks = [_build_inputs(session, chunk) for chunk in id_chunks]

    logger.info(
        "%d documents pending canonical-profile building, split into %d chunks of up to %d.",
        len(pending_ids),
        len(input_chunks),
        chunk_size,
    )

    client = OpenAI(api_key=settings.openai_api_key)

    total_saved = 0
    total_failed = 0
    for chunk_index, inputs in enumerate(input_chunks, start=1):
        if not inputs:
            continue
        logger.info("Chunk %d/%d: submitting batch for %d documents...", chunk_index, len(input_chunks), len(inputs))
        saved, failed = _run_chunk(client, settings.extraction_model, inputs)
        total_saved += saved
        total_failed += failed
        logger.info(
            "Chunk %d/%d done. saved=%d failed=%d (running total: saved=%d failed=%d)",
            chunk_index,
            len(input_chunks),
            saved,
            failed,
            total_saved,
            total_failed,
        )

    logger.info("All chunks done. total_saved=%d total_failed=%d", total_saved, total_failed)


if __name__ == "__main__":
    main()
