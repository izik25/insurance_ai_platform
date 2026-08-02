"""Judge cross-company match candidates by LLM reading comprehension.

Replaces lexical-word-overlap corroboration (core.matching.lexical) as the
decision mechanism: for every embedding-ranked candidate (see
core.matching.similarity.rank_candidates_by_company), an LLM reads both
documents' full extracted schema (not just appendix_name/coverage_type/
coverage_name) and judges whether they describe the same coverage in
substance. See core/matching/semantic_judge.py's docstring for why lexical
overlap alone isn't enough (the Phoenix "מרפא"-brand vs Hachshara "חשוכת
מרפא"-idiom collision).

Two-phase, since judging is a real (if cheap) OpenAI cost:

    python scripts/judge_matches.py --plan   # compute + print candidate-pair
                                              # count, no API calls, no cost
    python scripts/judge_matches.py --run    # judge concurrently, apply results

--run judges pairs via concurrent plain chat-completion calls (not the Batch
API - see core/matching/semantic_judge.py's docstring for why) and
checkpoints each verdict to data/processed/judge_checkpoint.jsonl as it
arrives, so re-running --run after an interruption resumes rather than
re-judging already-checkpointed pairs. Delete that file to force a full
re-judge.

Applying results recomputes the full match set from scratch and replaces all
previously auto-generated rows (auto_confirmed/pending_review) - never
touches rows a human has already reviewed via the dashboard (confirmed/
rejected), same guarantee as scripts/match_documents.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentEmbedding,
    DocumentExtraction,
    DocumentMatch,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.matching.semantic_judge import (  # noqa: E402
    DocumentJudgeInfo,
    JudgeVerdict,
    judge_pairs_concurrent,
)
from core.matching.similarity import DocumentMeta, rank_candidates_by_company  # noqa: E402
from core.models.enums import MatchStatus  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

_HUMAN_REVIEWED_STATUSES = (MatchStatus.CONFIRMED.value, MatchStatus.REJECTED.value)

# Plain concurrent chat-completion calls, not the Batch API - see
# core/matching/semantic_judge.py's module docstring for why (a single
# 800-pair *batch* took ~26 minutes, and the org's shared enqueued-token cap
# forces batches to run one at a time - the full ~78k-pair corpus would have
# taken 40+ hours). Checkpointed to a local file so a crash/interrupt loses
# at most the in-flight requests, not the whole run.
_CONCURRENCY = 150
_CHECKPOINT_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / (
    "judge_checkpoint.jsonl"
)


def _pair_id(doc_a: str, doc_b: str) -> str:
    a, b = sorted((doc_a, doc_b))
    return f"{a}|||{b}"


def _load_checkpoint() -> dict[str, JudgeVerdict | None]:
    if not _CHECKPOINT_PATH.exists():
        return {}
    verdicts: dict[str, JudgeVerdict | None] = {}
    with _CHECKPOINT_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            verdicts[entry["pair_id"]] = (
                JudgeVerdict(verdict=entry["verdict"], reasoning=entry["reasoning"])
                if entry["verdict"] is not None
                else None
            )
    return verdicts


def _load_data() -> tuple[
    dict[str, list[float]], dict[str, DocumentMeta], dict[str, DocumentJudgeInfo]
]:
    with session_scope() as session:
        embeddings_by_doc = {
            row.document_id: row.embedding for row in session.scalars(select(DocumentEmbedding))
        }
        extractions_by_doc = {
            row.document_id: row for row in session.scalars(select(DocumentExtraction))
        }
        documents = list(session.scalars(select(Document)))

    doc_meta: dict[str, DocumentMeta] = {}
    judge_info: dict[str, DocumentJudgeInfo] = {}
    for d in documents:
        extraction = extractions_by_doc.get(d.id)
        doc_meta[d.id] = DocumentMeta(
            company_id=d.company_id,
            domain=d.domain,
            appendix_name=d.appendix_name,
            coverage_type=extraction.coverage_type if extraction else None,
            coverage_name=extraction.coverage_name if extraction else None,
        )
        judge_info[d.id] = DocumentJudgeInfo(
            company_id=d.company_id,
            appendix_name=d.appendix_name,
            coverage_type=extraction.coverage_type if extraction else None,
            coverage_name=extraction.coverage_name if extraction else None,
            eligibility_conditions=extraction.eligibility_conditions if extraction else None,
            qualifying_period=extraction.qualifying_period if extraction else None,
            waiting_period=extraction.waiting_period if extraction else None,
            age_range=extraction.age_range if extraction else None,
            survival_period=extraction.survival_period if extraction else None,
            insurance_amounts=extraction.insurance_amounts if extraction else [],
            exclusions=extraction.exclusions if extraction else [],
            restrictions=extraction.restrictions if extraction else [],
            disease_list=extraction.disease_list if extraction else [],
        )
    return embeddings_by_doc, doc_meta, judge_info


def _build_pairs(
    candidates_by_doc: dict[str, dict[str, list[tuple[str, float]]]],
    judge_info: dict[str, DocumentJudgeInfo],
) -> dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]]:
    pairs: dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]] = {}
    for document_id, by_company in candidates_by_doc.items():
        for candidates in by_company.values():
            for candidate_id, _score in candidates:
                pid = _pair_id(document_id, candidate_id)
                if pid not in pairs:
                    a, b = sorted((document_id, candidate_id))
                    pairs[pid] = (judge_info[a], judge_info[b])
    return pairs


def plan() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_db()

    embeddings_by_doc, doc_meta, judge_info = _load_data()
    if not embeddings_by_doc:
        logger.info("No embeddings yet - nothing to judge.")
        return

    candidates_by_doc = rank_candidates_by_company(embeddings_by_doc, doc_meta)
    pairs = _build_pairs(candidates_by_doc, judge_info)

    total_candidates = sum(
        len(cands) for by_company in candidates_by_doc.values() for cands in by_company.values()
    )
    print(f"Documents with >=1 candidate: {len(candidates_by_doc)}")
    print(f"Total (document, company) candidate slots: {total_candidates}")
    print(f"Unique pairs to send to the judge (deduped): {len(pairs)}")


async def _judge_all(pairs: dict[str, tuple[DocumentJudgeInfo, DocumentJudgeInfo]]) -> None:
    """Judge every not-yet-checkpointed pair, appending each result to
    `_CHECKPOINT_PATH` as it completes (so an interrupt loses at most the
    requests still in flight, not the whole run)."""
    settings = get_settings()
    checkpointed = _load_checkpoint()
    # Only a real verdict counts as "done" - a checkpointed `None` means a
    # prior run exhausted its retries (e.g. the rate-limiter-less attempt
    # that 429'd every request), and should be retried, not skipped forever.
    already_done = {pid for pid, verdict in checkpointed.items() if verdict is not None}
    remaining = {pid: pair for pid, pair in pairs.items() if pid not in already_done}
    logger.info(
        "%d pairs already judged (resumed from checkpoint), %d remaining",
        len(already_done),
        len(remaining),
    )
    if not remaining:
        return

    _CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    completed = 0

    with _CHECKPOINT_PATH.open("a", encoding="utf-8") as handle:

        def on_result(pair_id: str, verdict: JudgeVerdict | None) -> None:
            nonlocal completed
            handle.write(
                json.dumps(
                    {
                        "pair_id": pair_id,
                        "verdict": verdict.verdict if verdict else None,
                        "reasoning": verdict.reasoning if verdict else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.flush()
            completed += 1
            if completed % 1000 == 0:
                logger.info("Judged %d/%d remaining pairs", completed, len(remaining))

        await judge_pairs_concurrent(
            settings.extraction_model,
            remaining,
            _CONCURRENCY,
            on_result,
            api_key=settings.openai_api_key,
        )

    logger.info("Judging done: %d pairs judged this run", completed)


def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_db()

    embeddings_by_doc, doc_meta, judge_info = _load_data()
    if not embeddings_by_doc:
        logger.info("No embeddings yet - nothing to judge.")
        return

    candidates_by_doc = rank_candidates_by_company(embeddings_by_doc, doc_meta)
    pairs = _build_pairs(candidates_by_doc, judge_info)
    logger.info(
        "Judging %d unique pairs with %d concurrent requests (checkpointed to %s)",
        len(pairs),
        _CONCURRENCY,
        _CHECKPOINT_PATH,
    )

    asyncio.run(_judge_all(pairs))
    verdicts = _load_checkpoint()

    with session_scope() as session:
        protected_ids = set(
            session.scalars(
                select(DocumentMatch.id).where(DocumentMatch.status.in_(_HUMAN_REVIEWED_STATUSES))
            )
        )

    with session_scope() as session:
        session.execute(
            delete(DocumentMatch).where(DocumentMatch.status.notin_(_HUMAN_REVIEWED_STATUSES))
        )

    auto_confirmed = 0
    pending_review = 0
    rejected_by_judge = 0
    skipped_protected = 0
    missing_verdict = 0

    for document_id, by_company in candidates_by_doc.items():
        for candidates in by_company.values():
            chosen: tuple[str, float] | None = None
            best_uncertain: tuple[str, float] | None = None
            for candidate_id, score in candidates:
                verdict = verdicts.get(_pair_id(document_id, candidate_id))
                if verdict is None:
                    missing_verdict += 1
                    continue
                if verdict.verdict == "same_coverage":
                    chosen = (candidate_id, score)
                    break
                if verdict.verdict == "uncertain" and best_uncertain is None:
                    best_uncertain = (candidate_id, score)
                if verdict.verdict == "different_coverage":
                    rejected_by_judge += 1

            if chosen is not None:
                candidate_id, score = chosen
                status = MatchStatus.AUTO_CONFIRMED
            elif best_uncertain is not None:
                candidate_id, score = best_uncertain
                status = MatchStatus.PENDING_REVIEW
            else:
                continue

            match_id = f"{document_id}:{candidate_id}"
            if match_id in protected_ids:
                skipped_protected += 1
                continue
            row = DocumentMatch(
                id=match_id,
                document_id=document_id,
                matched_document_id=candidate_id,
                similarity_score=score,
                status=status.value,
            )
            with session_scope() as session:
                session.merge(row)
            if status == MatchStatus.AUTO_CONFIRMED:
                auto_confirmed += 1
            else:
                pending_review += 1

    logger.info(
        "Done. auto_confirmed=%d pending_review=%d rejected_by_judge=%d "
        "skipped_protected=%d missing_verdict=%d",
        auto_confirmed,
        pending_review,
        rejected_by_judge,
        skipped_protected,
        missing_verdict,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true", help="Print pair counts, no API calls.")
    group.add_argument("--run", action="store_true", help="Submit the batch and apply results.")
    args = parser.parse_args()

    if args.plan:
        plan()
    else:
        run()


if __name__ == "__main__":
    main()
