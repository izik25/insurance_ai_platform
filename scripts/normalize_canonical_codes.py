"""Phase 2: normalize each document's Canonical Coverage Profile phrases into
core/knowledge_base's canonical codes.

Two passes (core/canonical/code_normalizer.py): rule-based substring match
first (free, no LLM call), then a single small LLM-assisted batch call per
document for whatever the rule-based pass couldn't map - never one call per
phrase. Phrases neither pass can map to an existing code are simply left
unmapped (no DocumentCanonicalCode row), not invented as a new code.

Usage: python scripts/normalize_canonical_codes.py [--limit N] [--chunk-size N]
       [--company migdal,phoenix] [--category-prefix health.critical_illness]
       [--no-llm-fallback]
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

from core.canonical.code_normalizer import (  # noqa: E402
    CandidatePhrase,
    RuleBasedMatch,
    collect_fallback_results,
    extract_candidate_phrases,
    normalize_rule_based,
    submit_fallback_batch,
    wait_for_batch,
)
from core.canonical.schema import CODES_VERSION, CanonicalCoverageProfile  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentCanonicalCode,
    DocumentCanonicalProfile,
    DocumentClassification,
    DocumentExtraction,
    DocumentPipelineStatus,
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
                DocumentPipelineStatus.canonical_codes_version == CODES_VERSION
            )
        )
    )
    profiled = set(
        session.scalars(
            select(DocumentPipelineStatus.document_id).where(
                DocumentPipelineStatus.canonical_profile_at.is_not(None)
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
        if document_id in already_current or document_id not in profiled:
            continue
        if category_prefix and not category_id.startswith(category_prefix):
            continue
        pending.append(document_id)
    return pending


def _save_rule_based_matches(document_id: str, matches: list[RuleBasedMatch]) -> None:
    """Saves rule-based DocumentCanonicalCode rows only - does NOT mark
    document_pipeline_status as done. A document with unmatched phrases
    still has its (more failure-prone, network-dependent) LLM fallback
    pass pending; marking it done here would make a re-run after a crash
    in that pass silently skip it and permanently lose those matches
    (confirmed live: a transient DNS/connection error during the fallback
    batch poll is exactly the failure mode this guards against)."""
    with session_scope() as session:
        session.query(DocumentCanonicalCode).filter_by(document_id=document_id).delete()
        for match in matches:
            session.add(
                DocumentCanonicalCode(
                    document_id=document_id,
                    code_category=match.code_category,
                    code=match.code,
                    raw_phrase=match.raw_phrase,
                    source_field=match.source_field,
                )
            )


def _mark_normalized(document_id: str) -> None:
    with session_scope() as session:
        status = session.get(DocumentPipelineStatus, document_id)
        if status is None:
            status = DocumentPipelineStatus(document_id=document_id)
        status.canonical_codes_at = datetime.now(UTC)
        status.canonical_codes_version = CODES_VERSION
        session.merge(status)


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--chunk-size", type=int, default=100)
    arg_parser.add_argument("--company", type=str, default=None)
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    arg_parser.add_argument("--no-llm-fallback", action="store_true")
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        pending_ids = _pending_documents(session, companies, args.category_prefix)
        if args.limit is not None:
            pending_ids = pending_ids[: args.limit]
        if not pending_ids:
            logger.info("Nothing to normalize - all matching documents already have canonical codes.")
            return

        candidates_by_doc: dict[str, list[CandidatePhrase]] = {}
        for document_id in pending_ids:
            profile_row = session.scalar(
                select(DocumentCanonicalProfile).where(DocumentCanonicalProfile.document_id == document_id)
            )
            extraction = session.scalar(
                select(DocumentExtraction).where(DocumentExtraction.document_id == document_id)
            )
            if profile_row is None or extraction is None:
                continue
            profile = CanonicalCoverageProfile.model_validate(profile_row.raw_profile)
            candidates_by_doc[document_id] = extract_candidate_phrases(profile, extraction)

    logger.info("%d documents pending canonical-code normalization.", len(candidates_by_doc))

    total_rule_based = 0
    total_llm_fallback = 0
    unmatched_by_doc: dict[str, list[CandidatePhrase]] = {}

    for document_id, candidates in candidates_by_doc.items():
        matched, unmatched = normalize_rule_based(candidates)
        total_rule_based += len(matched)
        _save_rule_based_matches(document_id, matched)
        if unmatched and not args.no_llm_fallback:
            unmatched_by_doc[document_id] = unmatched
        else:
            # Nothing left pending for this document - either everything
            # matched rule-based, or the fallback pass was explicitly skipped.
            _mark_normalized(document_id)

    logger.info(
        "Rule-based pass: %d codes matched across %d documents; %d documents have unmatched phrases.",
        total_rule_based,
        len(candidates_by_doc),
        len(unmatched_by_doc),
    )

    if unmatched_by_doc:
        if not settings.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is not set (needed for the LLM fallback pass)")
        client = OpenAI(api_key=settings.openai_api_key)

        chunk_size = args.chunk_size
        doc_ids = list(unmatched_by_doc.keys())
        for i in range(0, len(doc_ids), chunk_size):
            chunk_ids = doc_ids[i : i + chunk_size]
            chunk_docs = {doc_id: unmatched_by_doc[doc_id] for doc_id in chunk_ids}
            batch_id = submit_fallback_batch(client, settings.extraction_model, chunk_docs)
            if batch_id is None:
                for document_id in chunk_ids:
                    _mark_normalized(document_id)
                continue
            wait_for_batch(client, batch_id)
            fallback_results = collect_fallback_results(client, batch_id, chunk_docs)
            for document_id, matches in fallback_results.items():
                total_llm_fallback += len(matches)
                if matches:
                    with session_scope() as session:
                        for match in matches:
                            session.add(
                                DocumentCanonicalCode(
                                    document_id=document_id,
                                    code_category=match.code_category,
                                    code=match.code,
                                    raw_phrase=match.raw_phrase,
                                    source_field=match.source_field,
                                )
                            )
                # Only mark documents actually present in this chunk's
                # results as done - if the batch call itself raised (e.g.
                # the transient httpx.ConnectError seen live during a real
                # run), the exception propagates before this loop runs at
                # all and nothing in the chunk gets marked, so a re-run
                # correctly retries the whole chunk's fallback pass.
                _mark_normalized(document_id)

    logger.info(
        "Done. rule_based_matches=%d llm_fallback_matches=%d", total_rule_based, total_llm_fallback
    )


if __name__ == "__main__":
    main()
