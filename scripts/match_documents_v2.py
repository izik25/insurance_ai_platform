"""Phase 4: two-stage, category-scoped, explainable cross-company matching.

Does NOT touch scripts/match_documents.py or scripts/judge_matches.py -
those keep working exactly as before. This is a new, additive engine that
writes into the same document_matches table (same full-recompute-and-
replace-except-human-reviewed-rows pattern as the existing scripts), using
the new nullable columns (final_score, score_breakdown, critical_mismatches,
missing_features, best/second_candidate_score, candidate_margin,
mutual_match, group_validation_status, match_stage, matching_profile_version,
auditor_verdict, auditor_reasoning) added in the taxonomy/matching upgrade -
while keeping similarity_score/status populated too, so nothing downstream
(api/routes.py, the dashboard) needs to change to keep working.

Stage A (fast, no LLM): core.matching.candidate_index narrows each source
document to its own taxonomy category; core.matching.hard_constraints
disqualifies categorically-incompatible candidates; core.matching.
quantitative_score produces an explainable, weighted score from
DocumentFingerprint + DocumentCanonicalCode data (Jaccard over code sets,
normalized distance over numeric fields, exact/compatible/mismatch over
enums - never an LLM guess). The existing whole-document embedding
(document_embeddings, built from PolicyExtraction fields - the "old
schema") is also looked up per pair and recorded in score_breakdown as a
`semantic_embedding_similarity` entry, so the established embedding signal
stays visible and auditable alongside the new quantitative dimensions, even
though v1 doesn't blend it into the weighted final_score (no calibration
data yet to justify a specific weight for it - see scripts/calibrate_matching.py).

Stage B (deep verification, LLM) only runs for the pairs Stage A's category
profile places in the "deep_verification" score band - reuses the EXISTING
core.matching.semantic_judge.judge_pairs_concurrent unmodified, exactly the
way scripts/judge_matches.py already does, rather than building a second
LLM-judge implementation.

Usage: python scripts/match_documents_v2.py [--company migdal,phoenix]
       [--category-prefix health.critical_illness] [--limit N]
       [--skip-deep-verification]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentCanonicalCode,
    DocumentClassification,
    DocumentEmbedding,
    DocumentExtraction,
    DocumentFingerprint,
    DocumentMatch,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError  # noqa: E402
from core.matching.candidate_index import CandidateIndex, CandidateIndexEntry  # noqa: E402
from core.matching.hard_constraints import FingerprintFeatures  # noqa: E402
from core.matching.mutual_validation import compute_group_validation, compute_mutual_matches  # noqa: E402
from core.matching.orchestrator import (  # noqa: E402
    DEEP_VERIFIED,
    NEEDS_DEEP_VERIFICATION,
    MatchOutcome,
    run_stage_a,
)
from core.matching.semantic_judge import DocumentJudgeInfo, JudgeVerdict, judge_pairs_concurrent  # noqa: E402
from core.models.enums import MatchStatus  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

_HUMAN_REVIEWED_STATUSES = (MatchStatus.CONFIRMED.value, MatchStatus.REJECTED.value)
_JUDGE_CONCURRENCY = 100


def _load_features_and_index(
    session: Session, companies: list[str] | None, category_prefix: str | None
) -> tuple[dict[str, FingerprintFeatures], CandidateIndex]:
    query = (
        select(Document, DocumentClassification, DocumentFingerprint)
        .join(DocumentClassification, DocumentClassification.document_id == Document.id)
        .join(DocumentFingerprint, DocumentFingerprint.document_id == Document.id)
    )
    if companies:
        query = query.where(Document.company_id.in_(companies))
    if category_prefix:
        query = query.where(DocumentClassification.category_id.like(f"{category_prefix}%"))
    rows = session.execute(query).all()
    document_ids = [document.id for document, _c, _f in rows]

    codes_by_doc: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    if document_ids:
        code_rows = session.execute(
            select(
                DocumentCanonicalCode.document_id,
                DocumentCanonicalCode.code_category,
                DocumentCanonicalCode.code,
            ).where(DocumentCanonicalCode.document_id.in_(document_ids))
        )
        for document_id, code_category, code in code_rows:
            codes_by_doc[document_id][code_category].add(code)

    features_by_id: dict[str, FingerprintFeatures] = {}
    index_entries: list[CandidateIndexEntry] = []
    for document, classification, fingerprint in rows:
        features_by_id[document.id] = FingerprintFeatures(
            document_id=document.id,
            company_id=document.company_id,
            category_id=classification.category_id,
            main_category=fingerprint.main_category,
            coverage_family=fingerprint.coverage_family,
            coverage_subtype=fingerprint.coverage_subtype,
            benefit_model=fingerprint.benefit_model,
            target_population=fingerprint.target_population,
            alternative_categories=list(classification.alternative_categories or []),
            waiting_period_days=fingerprint.waiting_period_days,
            qualifying_period_days=fingerprint.qualifying_period_days,
            survival_period_days=fingerprint.survival_period_days,
            min_entry_age=fingerprint.min_entry_age,
            max_entry_age=fingerprint.max_entry_age,
            benefit_type=fingerprint.benefit_type,
            benefit_amount_min=fingerprint.benefit_amount_min,
            benefit_amount_max=fingerprint.benefit_amount_max,
            maximum_benefit=fingerprint.maximum_benefit,
            deductible_amount=fingerprint.deductible_amount,
            codes=dict(codes_by_doc.get(document.id, {})),
        )
        index_entries.append(
            CandidateIndexEntry(document.id, document.company_id, classification.category_id, document.is_active)
        )

    return features_by_id, CandidateIndex(index_entries)


def _embedding_similarity(
    embeddings_by_doc: dict[str, np.ndarray], document_id: str, matched_document_id: str
) -> float | None:
    a = embeddings_by_doc.get(document_id)
    b = embeddings_by_doc.get(matched_document_id)
    if a is None or b is None:
        return None
    return float(np.dot(a, b))  # pre-normalized vectors (core.embeddings.model.embed_texts)


def _build_judge_info(session: Session, document_ids: set[str]) -> dict[str, DocumentJudgeInfo]:
    if not document_ids:
        return {}
    info: dict[str, DocumentJudgeInfo] = {}
    rows = session.execute(
        select(Document, DocumentExtraction)
        .join(DocumentExtraction, DocumentExtraction.document_id == Document.id)
        .where(Document.id.in_(document_ids))
    )
    for document, extraction in rows:
        info[document.id] = DocumentJudgeInfo(
            company_id=document.company_id,
            appendix_name=document.appendix_name,
            coverage_type=extraction.coverage_type,
            coverage_name=extraction.coverage_name,
            eligibility_conditions=extraction.eligibility_conditions,
            qualifying_period=extraction.qualifying_period,
            waiting_period=extraction.waiting_period,
            age_range=extraction.age_range,
            survival_period=extraction.survival_period,
            insurance_amounts=extraction.insurance_amounts,
            exclusions=extraction.exclusions,
            restrictions=extraction.restrictions,
            disease_list=extraction.disease_list,
        )
    return info


def _run_deep_verification(
    model: str, api_key: str, pending: list[MatchOutcome], judge_info: dict[str, DocumentJudgeInfo]
) -> list[MatchOutcome]:
    """Judges every `pending` (NEEDS_DEEP_VERIFICATION) outcome and returns
    the resolved list - same_coverage upgrades to auto_confirmed,
    different_coverage drops the outcome entirely, uncertain (or a failed
    judge call) falls back to pending_review. Reuses
    core.matching.semantic_judge.judge_pairs_concurrent unmodified."""
    pairs: dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]] = {}
    pair_id_to_outcome: dict[str, MatchOutcome] = {}
    for outcome in pending:
        doc_a = judge_info.get(outcome.document_id)
        doc_b = judge_info.get(outcome.matched_document_id)
        if doc_a is None or doc_b is None:
            continue
        pair_id = f"{outcome.document_id}:{outcome.matched_document_id}"
        pairs[pair_id] = (doc_a, doc_b)
        pair_id_to_outcome[pair_id] = outcome

    if not pairs:
        return []

    logger.info("Stage B: sending %d pairs to the LLM judge for deep verification.", len(pairs))
    verdicts: dict[str, JudgeVerdict | None] = {}

    def _on_result(pair_id: str, verdict: JudgeVerdict | None) -> None:
        verdicts[pair_id] = verdict
        if len(verdicts) % 25 == 0:
            logger.info("Stage B progress: %d/%d judged", len(verdicts), len(pairs))

    asyncio.run(judge_pairs_concurrent(model, pairs, _JUDGE_CONCURRENCY, _on_result, api_key))

    resolved: list[MatchOutcome] = []
    for pair_id, outcome in pair_id_to_outcome.items():
        verdict = verdicts.get(pair_id)
        if verdict is None:
            outcome.status = "pending_review"
            resolved.append(outcome)
            continue
        outcome.match_stage = DEEP_VERIFIED
        outcome.auditor_verdict = verdict.verdict
        outcome.auditor_reasoning = verdict.reasoning
        if verdict.verdict == "same_coverage":
            outcome.status = "auto_confirmed"
            resolved.append(outcome)
        elif verdict.verdict == "uncertain":
            outcome.status = "pending_review"
            resolved.append(outcome)
        # different_coverage: dropped entirely - not appended to `resolved`.
    return resolved


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--company", type=str, default=None, help="Comma-separated company_id list")
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    arg_parser.add_argument("--limit", type=int, default=None, help="Cap on source documents (for a pilot run)")
    arg_parser.add_argument("--skip-deep-verification", action="store_true")
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        features_by_id, index = _load_features_and_index(session, companies, args.category_prefix)
        if args.limit is not None and len(features_by_id) > args.limit:
            keep_ids = set(list(features_by_id.keys())[: args.limit])
            features_by_id = {k: v for k, v in features_by_id.items() if k in keep_ids}

        embeddings_by_doc = {
            row.document_id: np.array(row.embedding)
            for row in session.scalars(
                select(DocumentEmbedding).where(DocumentEmbedding.document_id.in_(features_by_id.keys()))
            )
        }

    logger.info("%d documents in scope for this run.", len(features_by_id))
    if not features_by_id:
        logger.info("Nothing to match - no documents matched the given filters.")
        return

    outcomes = run_stage_a(features_by_id, index)
    logger.info("Stage A produced %d candidate outcomes.", len(outcomes))

    fast_final = [o for o in outcomes if o.match_stage != NEEDS_DEEP_VERIFICATION]
    needs_deep = [o for o in outcomes if o.match_stage == NEEDS_DEEP_VERIFICATION]

    if needs_deep and not args.skip_deep_verification:
        if not settings.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is not set (needed for Stage B deep verification)")
        judge_ids = {o.document_id for o in needs_deep} | {o.matched_document_id for o in needs_deep}
        with session_scope() as session:
            judge_info = _build_judge_info(session, judge_ids)
        deep_resolved = _run_deep_verification(
            settings.extraction_model, settings.openai_api_key, needs_deep, judge_info
        )
        logger.info(
            "Stage B resolved %d/%d pairs (rest dropped as different_coverage).",
            len(deep_resolved),
            len(needs_deep),
        )
        final_outcomes = fast_final + deep_resolved
    elif needs_deep:
        logger.info("--skip-deep-verification set: %d pairs kept as pending_review without an LLM look.", len(needs_deep))
        final_outcomes = fast_final + needs_deep
    else:
        final_outcomes = fast_final

    for outcome in final_outcomes:
        similarity = _embedding_similarity(embeddings_by_doc, outcome.document_id, outcome.matched_document_id)
        if similarity is not None:
            outcome.score_breakdown["semantic_embedding_similarity"] = round(similarity, 4)

    pair_set = {(o.document_id, o.matched_document_id) for o in final_outcomes}
    mutual = compute_mutual_matches(pair_set)
    mutual_pairs = {pair for pair, is_mutual in mutual.items() if is_mutual}
    group_status = compute_group_validation(mutual_pairs, pair_set)

    with session_scope() as session:
        protected_ids = set(
            session.scalars(
                select(DocumentMatch.id).where(DocumentMatch.status.in_(_HUMAN_REVIEWED_STATUSES))
            )
        )
        scope_ids = set(features_by_id.keys())
        existing_in_scope = session.execute(
            select(DocumentMatch.id, DocumentMatch.document_id, DocumentMatch.matched_document_id).where(
                DocumentMatch.status.notin_(_HUMAN_REVIEWED_STATUSES)
            )
        ).all()
        deletable_ids = [
            match_id
            for match_id, doc_id, matched_id in existing_in_scope
            if doc_id in scope_ids and matched_id in scope_ids
        ]
        if deletable_ids:
            session.execute(delete(DocumentMatch).where(DocumentMatch.id.in_(deletable_ids)))

    saved = 0
    skipped_protected = 0
    for outcome in final_outcomes:
        match_id = f"{outcome.document_id}:{outcome.matched_document_id}"
        if match_id in protected_ids:
            skipped_protected += 1
            continue
        pair = (outcome.document_id, outcome.matched_document_id)
        row = DocumentMatch(
            id=match_id,
            document_id=outcome.document_id,
            matched_document_id=outcome.matched_document_id,
            similarity_score=outcome.final_score,
            status=outcome.status,
            final_score=outcome.final_score,
            score_breakdown=outcome.score_breakdown,
            critical_mismatches=outcome.critical_mismatches,
            missing_features=outcome.missing_features,
            best_candidate_score=outcome.best_candidate_score,
            second_candidate_score=outcome.second_candidate_score,
            candidate_margin=outcome.candidate_margin,
            mutual_match=mutual.get(pair, False),
            group_validation_status=group_status.get(pair),
            match_stage=outcome.match_stage,
            hard_constraint_failures=outcome.hard_constraint_failures,
            matching_profile_version=outcome.matching_profile_version,
            auditor_verdict=outcome.auditor_verdict,
            auditor_reasoning=outcome.auditor_reasoning,
        )
        with session_scope() as session:
            session.merge(row)
        saved += 1

    logger.info(
        "Done. saved=%d skipped_protected=%d auto_confirmed=%d pending_review=%d",
        saved,
        skipped_protected,
        sum(1 for o in final_outcomes if o.status == "auto_confirmed"),
        sum(1 for o in final_outcomes if o.status == "pending_review"),
    )


if __name__ == "__main__":
    main()
