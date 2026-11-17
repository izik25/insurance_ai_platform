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
       [--document-ids ID1,ID2 | --document-ids-file ids.txt]

--document-ids restricts BOTH the source documents and the candidate pool to
exactly that set (a closed pilot: e.g. 10 documents already rebuilt at the
current fingerprint version, matched only against each other). Only pairs
inside the set are deleted/rewritten, as for any other scoped run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import asdict
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
    DocumentQuestionAnswer,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import ConfigurationError  # noqa: E402
from core.knowledge_base.registry import get_questions_for_category  # noqa: E402
from core.matching.candidate_index import CandidateIndex, CandidateIndexEntry  # noqa: E402
from core.matching.hard_constraints import FingerprintFeatures  # noqa: E402
from core.matching.mutual_validation import (  # noqa: E402
    compute_group_validation,
    compute_mutual_matches,
)
from core.matching.orchestrator import (  # noqa: E402
    DEEP_VERIFIED,
    NEEDS_DEEP_VERIFICATION,
    MatchOutcome,
    run_stage_a,
)
from core.matching.semantic_judge import (  # noqa: E402
    DocumentJudgeInfo,
    JudgeAnswer,
    JudgeVerdict,
    judge_pairs_concurrent,
)
from core.models.enums import MatchStatus  # noqa: E402
from core.utils.document_selection import read_document_ids  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

_HUMAN_REVIEWED_STATUSES = (MatchStatus.CONFIRMED.value, MatchStatus.REJECTED.value)
_JUDGE_CONCURRENCY = 100


def _load_features_and_index(
    session: Session,
    companies: list[str] | None,
    category_prefix: str | None,
    document_ids: set[str] | None = None,
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
    if document_ids is not None:
        query = query.where(Document.id.in_(document_ids))
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


# Fingerprint fields shown to the judge next to the answers.
_JUDGE_FINGERPRINT_FIELDS = (
    ("waiting_period_days", "תקופת המתנה (ימים)"),
    ("qualifying_period_days", "תקופת אכשרה (ימים)"),
    ("survival_period_days", "תקופת הישרדות (ימים)"),
    ("min_entry_age", "גיל כניסה מינימלי"),
    ("max_entry_age", "גיל כניסה מקסימלי"),
    ("termination_age", "גיל סיום כיסוי"),
    ("benefit_type", "סוג תגמול"),
    ("benefit_amount_max", "סכום ביטוח מקסימלי"),
    ("maximum_benefit", "תקרת תגמול"),
    ("target_population", "אוכלוסיית יעד"),
)


def _build_judge_info(session: Session, document_ids: set[str]) -> dict[str, DocumentJudgeInfo]:
    """The judge's view of each document: its question-bank answers (the
    same questions for every document in a category, answered from the full
    text) plus its fingerprint. Falls back to the old extraction fields only
    for a document that has no question-bank answers yet."""
    if not document_ids:
        return {}
    info: dict[str, DocumentJudgeInfo] = {}

    answers_by_doc: dict[str, list[DocumentQuestionAnswer]] = defaultdict(list)
    for answer in session.scalars(
        select(DocumentQuestionAnswer).where(DocumentQuestionAnswer.document_id.in_(document_ids))
    ):
        answers_by_doc[answer.document_id].append(answer)
    category_by_doc = dict(
        session.execute(
            select(DocumentClassification.document_id, DocumentClassification.category_id).where(
                DocumentClassification.document_id.in_(document_ids)
            )
        ).all()
    )
    fingerprint_by_doc = {
        fp.document_id: fp
        for fp in session.scalars(
            select(DocumentFingerprint).where(DocumentFingerprint.document_id.in_(document_ids))
        )
    }

    for document in session.scalars(select(Document).where(Document.id.in_(document_ids))):
        answers = answers_by_doc.get(document.id)
        if not answers:
            continue
        category_id = category_by_doc.get(document.id)
        question_text = (
            {q.question_id: q.text_he for q in get_questions_for_category(category_id)}
            if category_id
            else {}
        )
        fingerprint = fingerprint_by_doc.get(document.id)
        fingerprint_view = {}
        if fingerprint is not None:
            for field_name, label in _JUDGE_FINGERPRINT_FIELDS:
                value = getattr(fingerprint, field_name)
                if value is not None:
                    fingerprint_view[label] = f"{value:g}" if isinstance(value, float) else str(value)
        info[document.id] = DocumentJudgeInfo(
            company_id=document.company_id,
            appendix_name=document.appendix_name,
            category_id=category_id,
            fingerprint=fingerprint_view,
            answers=[
                JudgeAnswer(
                    question=question_text.get(a.question_id, a.question_id),
                    status=a.status,
                    answer=a.answer_text,
                )
                for a in sorted(answers, key=lambda a: a.question_id)
            ],
        )

    missing = document_ids - set(info)
    if not missing:
        return info
    rows = session.execute(
        select(Document, DocumentExtraction)
        .join(DocumentExtraction, DocumentExtraction.document_id == Document.id)
        .where(Document.id.in_(missing))
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
    (resolved, dropped) - same_coverage upgrades to auto_confirmed,
    different_coverage moves the outcome to `dropped` (not saved to
    document_matches, but kept for the audit file), uncertain (or a failed
    judge call, or a document the judge has no data for) stays
    pending_review.

    Each unordered pair is judged ONCE and the verdict applied to both
    directions (A->B and B->A are the same question; judging them separately
    cost twice as much and could disagree with itself). The judge's
    "document A" is the lexicographically smaller id; material differences
    are re-oriented so document_a is always the outcome's own document."""
    pair_key = lambda o: tuple(sorted((o.document_id, o.matched_document_id)))  # noqa: E731
    pairs: dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]] = {}
    unjudgeable: list[MatchOutcome] = []
    for outcome in pending:
        first, second = pair_key(outcome)
        if first not in judge_info or second not in judge_info:
            unjudgeable.append(outcome)
            continue
        pairs.setdefault(f"{first}|{second}", (judge_info[first], judge_info[second]))

    verdicts: dict[str, JudgeVerdict | None] = {}
    if pairs:
        logger.info(
            "Stage B: sending %d unique pairs (%d directed outcomes) to the LLM judge.",
            len(pairs),
            len(pending) - len(unjudgeable),
        )

        def _on_result(pair_id: str, verdict: JudgeVerdict | None) -> None:
            verdicts[pair_id] = verdict
            if len(verdicts) % 25 == 0:
                logger.info("Stage B progress: %d/%d judged", len(verdicts), len(pairs))

        asyncio.run(judge_pairs_concurrent(model, pairs, _JUDGE_CONCURRENCY, _on_result, api_key))

    resolved: list[MatchOutcome] = []
    dropped: list[MatchOutcome] = []
    for outcome in unjudgeable:
        outcome.status = "pending_review"
        resolved.append(outcome)
    unjudgeable_objects = {id(o) for o in unjudgeable}
    for outcome in pending:
        if id(outcome) in unjudgeable_objects:
            continue
        first, second = pair_key(outcome)
        verdict = verdicts.get(f"{first}|{second}")
        if verdict is None:
            outcome.status = "pending_review"
            resolved.append(outcome)
            continue
        outcome.match_stage = DEEP_VERIFIED
        outcome.auditor_verdict = verdict.verdict
        outcome.auditor_reasoning = verdict.reasoning
        own_is_a = outcome.document_id == first
        # The two axes the verdict is computed from, oriented to this
        # outcome (narrower_in_a = narrower in outcome.document_id).
        event = verdict.core_insured_event
        if not own_is_a and event in ("narrower_in_a", "narrower_in_b"):
            event = "narrower_in_b" if event == "narrower_in_a" else "narrower_in_a"
        outcome.score_breakdown["judge"] = {
            "core_insured_event": event,
            "core_payment_mechanism": verdict.core_payment_mechanism,
        }
        outcome.material_differences = [
            {
                "topic": d.topic,
                "document_a": d.document_a if own_is_a else d.document_b,
                "document_b": d.document_b if own_is_a else d.document_a,
            }
            for d in verdict.material_differences
        ]
        if verdict.verdict == "same_coverage":
            outcome.status = "auto_confirmed"
            resolved.append(outcome)
        elif verdict.verdict == "uncertain":
            outcome.status = "pending_review"
            resolved.append(outcome)
        else:
            dropped.append(outcome)
    return resolved, dropped


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--company", type=str, default=None, help="Comma-separated company_id list")
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    arg_parser.add_argument("--limit", type=int, default=None, help="Cap on source documents (for a pilot run)")
    arg_parser.add_argument("--skip-deep-verification", action="store_true")
    arg_parser.add_argument("--document-ids", type=str, default=None, help="Comma-separated document ids")
    arg_parser.add_argument("--document-ids-file", type=Path, default=None, help="One document id per line")
    arg_parser.add_argument(
        "--auto-confirm-on-score",
        action="store_true",
        help="Old policy: auto-confirm high-score pairs without the judge",
    )
    arg_parser.add_argument(
        "--audit-file",
        type=Path,
        default=None,
        help="Write every Stage A outcome (incl. pairs the judge dropped) as JSON",
    )
    args = arg_parser.parse_args()
    args.force = False  # read_document_ids() expects it; matching has no version cache to bypass

    settings = get_settings()
    configure_logging(settings)
    init_db()

    companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        features_by_id, index = _load_features_and_index(
            session, companies, args.category_prefix, read_document_ids(args)
        )
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

    outcomes = run_stage_a(features_by_id, index, args.auto_confirm_on_score)
    logger.info("Stage A produced %d candidate outcomes.", len(outcomes))
    dropped: list[MatchOutcome] = []

    fast_final = [o for o in outcomes if o.match_stage != NEEDS_DEEP_VERIFICATION]
    needs_deep = [o for o in outcomes if o.match_stage == NEEDS_DEEP_VERIFICATION]

    if needs_deep and not args.skip_deep_verification:
        if not settings.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is not set (needed for Stage B deep verification)")
        judge_ids = {o.document_id for o in needs_deep} | {o.matched_document_id for o in needs_deep}
        with session_scope() as session:
            judge_info = _build_judge_info(session, judge_ids)
        deep_resolved, dropped = _run_deep_verification(
            settings.judge_model, settings.openai_api_key, needs_deep, judge_info
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

    for outcome in final_outcomes + dropped:
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
            material_differences=outcome.material_differences,
        )
        with session_scope() as session:
            session.merge(row)
        saved += 1

    if args.audit_file:
        saved_objects = {id(o) for o in final_outcomes}
        audit = [
            {
                **asdict(o),
                "saved": id(o) in saved_objects,
                "mutual_match": mutual.get((o.document_id, o.matched_document_id)),
            }
            for o in final_outcomes + dropped
        ]
        args.audit_file.write_text(
            json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        logger.info(
            "Audit file written: %s (%d outcomes, %d dropped by the judge)",
            args.audit_file,
            len(audit),
            len(dropped),
        )

    logger.info(
        "Done. saved=%d skipped_protected=%d auto_confirmed=%d pending_review=%d",
        saved,
        skipped_protected,
        sum(1 for o in final_outcomes if o.status == "auto_confirmed"),
        sum(1 for o in final_outcomes if o.status == "pending_review"),
    )


if __name__ == "__main__":
    main()
