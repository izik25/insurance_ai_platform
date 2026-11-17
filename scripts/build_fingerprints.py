"""Phase 3: build the Quantitative Insurance Fingerprint for every document
that has a Canonical Coverage Profile.

Pure code - no LLM call, no network, no PDF/OCR read. Reads
DocumentClassification + DocumentCanonicalProfile (both already computed in
Phase 2), calls core/fingerprint/builder.build_fingerprint (deterministic),
and persists DocumentFingerprint. Because there's no LLM cost at all, this
is safe and cheap to re-run in full any time fingerprint_version bumps
(e.g. after a parser improvement) without touching the rest of the
pipeline.

Usage: python scripts/build_fingerprints.py [--limit N] [--company migdal,phoenix]
       [--category-prefix health.critical_illness]
       [--document-ids ID1,ID2 | --document-ids-file ids.txt] [--force]

--document-ids/--document-ids-file restrict the run to exactly those
documents; --force rebuilds them even if already current.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.canonical.schema import CanonicalCoverageProfile  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentCanonicalProfile,
    DocumentClassification,
    DocumentFingerprint,
    DocumentPipelineStatus,
    DocumentQuestionAnswer,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.fingerprint.builder import FINGERPRINT_VERSION, build_fingerprint  # noqa: E402
from core.knowledge_base.registry import (  # noqa: E402
    codes_mentioned,
    normalize_to_earliest_code,
)
from core.utils.document_selection import (  # noqa: E402
    add_document_selection_args,
    read_document_ids,
)
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _pending_documents(
    session: Session,
    companies: list[str] | None,
    category_prefix: str | None,
    document_ids: set[str] | None = None,
    force: bool = False,
) -> list[str]:
    already_current = set() if force else set(
        session.scalars(
            select(DocumentPipelineStatus.document_id).where(
                DocumentPipelineStatus.fingerprint_version == FINGERPRINT_VERSION
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

    if document_ids is not None:
        query = query.where(Document.id.in_(document_ids))

    pending = []
    for document_id, category_id in session.execute(query):
        if document_id in already_current or document_id not in profiled:
            continue
        if category_prefix and not category_id.startswith(category_prefix):
            continue
        pending.append(document_id)
    return pending


def _build_and_save(session: Session, document_id: str) -> bool:
    classification = session.scalar(
        select(DocumentClassification).where(DocumentClassification.document_id == document_id)
    )
    profile_row = session.scalar(
        select(DocumentCanonicalProfile).where(DocumentCanonicalProfile.document_id == document_id)
    )
    if classification is None or profile_row is None:
        return False

    profile = CanonicalCoverageProfile.model_validate(profile_row.raw_profile)
    benefit_type_code = None
    if profile.benefit_type:
        benefit_type_code = normalize_to_earliest_code(profile.benefit_type, "benefit_type")
        # A text naming several benefit types ("פיצוי חד-פעמי (במקרה מוות)
        # ושיפוי...") is settled by the classification's benefit_model when
        # it is one of them - the classifier judged the coverage as a whole.
        mentioned = codes_mentioned(profile.benefit_type, "benefit_type")
        if len(mentioned) > 1 and classification.benefit_model in mentioned:
            benefit_type_code = classification.benefit_model
    answers = session.scalars(
        select(DocumentQuestionAnswer).where(DocumentQuestionAnswer.document_id == document_id)
    ).all()
    answer_statuses = {a.question_id: a.status for a in answers}
    answer_texts = {a.question_id: (a.answer_text, a.evidence_text) for a in answers}

    result = build_fingerprint(
        main_category=classification.main_category,
        coverage_family=classification.coverage_family,
        coverage_subtype=classification.coverage_subtype,
        benefit_model=classification.benefit_model,
        target_population=classification.target_population,
        profile=profile,
        benefit_type_code=benefit_type_code,
        answer_statuses=answer_statuses or None,
        answer_texts=answer_texts or None,
    )

    row = DocumentFingerprint(
        document_id=document_id,
        fingerprint_version=FINGERPRINT_VERSION,
        main_category=result.main_category,
        coverage_family=result.coverage_family,
        coverage_subtype=result.coverage_subtype,
        benefit_model=result.benefit_model,
        target_population=result.target_population,
        waiting_period_days=result.waiting_period_days,
        waiting_period_raw=result.waiting_period_raw,
        qualifying_period_days=result.qualifying_period_days,
        qualifying_period_raw=result.qualifying_period_raw,
        survival_period_days=result.survival_period_days,
        survival_period_raw=result.survival_period_raw,
        min_entry_age=result.min_entry_age,
        max_entry_age=result.max_entry_age,
        termination_age=result.termination_age,
        age_raw=result.age_raw,
        benefit_type=result.benefit_type,
        benefit_amount_min=result.benefit_amount_min,
        benefit_amount_max=result.benefit_amount_max,
        benefit_amount_currency=result.benefit_amount_currency,
        amount_raw=result.amount_raw,
        benefit_percentage=result.benefit_percentage,
        maximum_benefit=result.maximum_benefit,
        deductible_amount=result.deductible_amount,
        covered_event_count=result.covered_event_count,
        major_exclusion_count=result.major_exclusion_count,
        special_condition_count=result.special_condition_count,
        raw_features=result.raw_features,
    )
    # merge() matches on the primary key, but row.id is freshly generated -
    # reuse the existing row's id so a rebuild updates it instead of
    # violating the unique document_id constraint.
    existing_id = session.scalar(
        select(DocumentFingerprint.id).where(DocumentFingerprint.document_id == document_id)
    )
    if existing_id is not None:
        row.id = existing_id
    session.merge(row)

    status = session.get(DocumentPipelineStatus, document_id)
    if status is None:
        status = DocumentPipelineStatus(document_id=document_id)
    status.fingerprint_at = datetime.now(UTC)
    status.fingerprint_version = FINGERPRINT_VERSION
    session.merge(status)
    return True


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument("--company", type=str, default=None)
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    add_document_selection_args(arg_parser)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        pending_ids = _pending_documents(
            session, companies, args.category_prefix, read_document_ids(args), args.force
        )
        if args.limit is not None:
            pending_ids = pending_ids[: args.limit]

        if not pending_ids:
            logger.info("Nothing to build - all matching documents already have a fingerprint.")
            return

        logger.info("%d documents pending fingerprint building.", len(pending_ids))
        built = 0
        skipped = 0
        for document_id in pending_ids:
            if _build_and_save(session, document_id):
                built += 1
            else:
                skipped += 1
            if built % 50 == 0 and built:
                logger.info("Progress: %d/%d built", built, len(pending_ids))

    logger.info("Done. built=%d skipped=%d", built, skipped)


if __name__ == "__main__":
    main()
