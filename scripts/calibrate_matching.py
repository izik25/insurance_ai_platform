"""Phase 3: analyze existing document_matches against the new
DocumentFingerprint/DocumentCanonicalCode data to inform matching-profile
weights - read-only, writes an audit row to match_calibration_runs and
prints a draft profile for human review. Never writes directly into
core/matching/profiles/data/.

IMPORTANT CALIBRATION-QUALITY CAVEAT (surfaced by this script's own run,
not assumed up front): across the entire corpus there is essentially no
human-reviewed ground truth to calibrate against - confirmed live, only 1
DocumentMatch row has status=confirmed and 0 have status=rejected out of
~16000 total; everything else is machine-generated (auto_confirmed via the
existing cosine+lexical pipeline, or pending_review). "Calibration" here
therefore cannot mean "learn weights from verified labels" - there aren't
enough of them. What this script does instead, honestly: checks whether
the NEW quantitative signals (canonical-code Jaccard overlap, period/age
gaps) are directionally consistent with the OLD pipeline's auto_confirmed
vs pending_review split, as a sanity check before Phase 4 builds the full
scoring engine on top of them. Every output is explicitly marked
provisional pending real human review via the dashboard.

Usage: python scripts/calibrate_matching.py --category-prefix health.critical_illness
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    DocumentCanonicalCode,
    DocumentClassification,
    DocumentFingerprint,
    DocumentMatch,
    MatchCalibrationRun,
)
from core.database.session import init_db, session_scope  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _jaccard(a: set[str], b: set[str]) -> float | None:
    if not a and not b:
        return None  # MISSING_BOTH - never silently counts as a match
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def _codes_by_category(session: Session, document_id: str) -> dict[str, set[str]]:
    rows = session.execute(
        select(DocumentCanonicalCode.code_category, DocumentCanonicalCode.code).where(
            DocumentCanonicalCode.document_id == document_id
        )
    ).all()
    result: dict[str, set[str]] = {}
    for category, code in rows:
        result.setdefault(category, set()).add(code)
    return result


def _period_gap_days(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return abs(a - b)


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--category-prefix", type=str, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    with session_scope() as session:
        query = select(DocumentClassification.document_id)
        if args.category_prefix:
            query = query.where(DocumentClassification.category_id.like(f"{args.category_prefix}%"))
        scope_ids = set(session.scalars(query))

        matches = [
            m
            for (m,) in session.execute(select(DocumentMatch)).all()
            if m.document_id in scope_ids and m.matched_document_id in scope_ids
        ]
        logger.info("%d document_matches with both sides in scope.", len(matches))

        status_counts: dict[str, int] = {}
        for m in matches:
            status_counts[m.status] = status_counts.get(m.status, 0) + 1
        logger.info("Status breakdown: %s", status_counts)
        human_reviewed = [m for m in matches if m.status in ("confirmed", "rejected")]
        logger.info(
            "Human-reviewed rows in scope: %d (confirmed=%d, rejected=%d) - %s",
            len(human_reviewed),
            sum(1 for m in human_reviewed if m.status == "confirmed"),
            sum(1 for m in human_reviewed if m.status == "rejected"),
            "SUFFICIENT for real calibration" if len(human_reviewed) >= 30 else
            "TOO FEW for real calibration - results below are directional only",
        )

        rows = []
        fingerprint_cache: dict[str, DocumentFingerprint | None] = {}
        codes_cache: dict[str, dict[str, set[str]]] = {}

        def get_fingerprint(doc_id: str) -> DocumentFingerprint | None:
            if doc_id not in fingerprint_cache:
                fingerprint_cache[doc_id] = session.scalar(
                    select(DocumentFingerprint).where(DocumentFingerprint.document_id == doc_id)
                )
            return fingerprint_cache[doc_id]

        def get_codes(doc_id: str) -> dict[str, set[str]]:
            if doc_id not in codes_cache:
                codes_cache[doc_id] = _codes_by_category(session, doc_id)
            return codes_cache[doc_id]

        for m in matches:
            fp_a, fp_b = get_fingerprint(m.document_id), get_fingerprint(m.matched_document_id)
            if fp_a is None or fp_b is None:
                continue
            codes_a, codes_b = get_codes(m.document_id), get_codes(m.matched_document_id)

            all_categories = set(codes_a) | set(codes_b)
            jaccards = {
                cat: _jaccard(codes_a.get(cat, set()), codes_b.get(cat, set())) for cat in all_categories
            }
            jaccards = {k: v for k, v in jaccards.items() if v is not None}
            avg_jaccard = mean(jaccards.values()) if jaccards else None

            waiting_gap = _period_gap_days(fp_a.waiting_period_days, fp_b.waiting_period_days)
            same_coverage_subtype = (
                fp_a.coverage_subtype == fp_b.coverage_subtype
                if fp_a.coverage_subtype and fp_b.coverage_subtype
                else None
            )
            same_benefit_type = (
                fp_a.benefit_type == fp_b.benefit_type if fp_a.benefit_type and fp_b.benefit_type else None
            )

            rows.append(
                {
                    "status": m.status,
                    "similarity_score": m.similarity_score,
                    "avg_jaccard": avg_jaccard,
                    "insured_event_jaccard": jaccards.get("insured_event"),
                    "exclusion_jaccard": jaccards.get("exclusion"),
                    "waiting_gap_days": waiting_gap,
                    "same_coverage_subtype": same_coverage_subtype,
                    "same_benefit_type": same_benefit_type,
                }
            )

        logger.info("%d pairs had fingerprints on both sides and were analyzed.", len(rows))

        def summarize(status: str, field: str) -> str:
            values = [r[field] for r in rows if r["status"] == status and r[field] is not None]
            if not values:
                return "n/a"
            return f"mean={mean(values):.3f} n={len(values)}"

        logger.info("=== avg_jaccard by status ===")
        for status in status_counts:
            logger.info("  %-16s %s", status, summarize(status, "avg_jaccard"))
        logger.info("=== insured_event_jaccard by status ===")
        for status in status_counts:
            logger.info("  %-16s %s", status, summarize(status, "insured_event_jaccard"))
        logger.info("=== waiting_gap_days by status ===")
        for status in status_counts:
            logger.info("  %-16s %s", status, summarize(status, "waiting_gap_days"))

        same_subtype_rate = {
            status: mean(
                [1.0 if r["same_coverage_subtype"] else 0.0 for r in rows if r["status"] == status and r["same_coverage_subtype"] is not None]
            )
            if any(r["status"] == status and r["same_coverage_subtype"] is not None for r in rows)
            else None
            for status in status_counts
        }
        logger.info("=== same_coverage_subtype rate by status === %s", same_subtype_rate)

        feature_importance = {
            "insured_event": "CRITICAL",
            "covered_events": "HIGH",
            "exclusions": "MEDIUM",
            "eligibility": "MEDIUM",
            "periods": "MEDIUM",
            "benefit": "HIGH",
            "definitions": "LOW",
        }
        weights_proposed = {
            "category": 0.20,
            "coverage": 0.10,
            "insured_event": 0.20,
            "covered_events": 0.15,
            "exclusions": 0.10,
            "eligibility": 0.05,
            "periods": 0.05,
            "benefit": 0.10,
            "definitions": 0.05,
        }
        hard_constraints_proposed = [
            "main_category_mismatch",
            "coverage_family_mismatch",
            "benefit_model_incompatible",
            "target_population_incompatible",
        ]
        notes = (
            f"PROVISIONAL - only {len(human_reviewed)} human-reviewed (confirmed/rejected) "
            f"document_matches rows exist in this category scope (target for real calibration: "
            f"30+ per category, ideally with both confirmed AND rejected examples). Weights above "
            "are the same domain-informed defaults as core/matching/profiles/data/default.v1.yaml, "
            "NOT learned from labeled data. The Jaccard/period-gap breakdown by status (see log) is "
            "a directional sanity check only: does the new quantitative signal separate the OLD "
            "pipeline's auto_confirmed vs pending_review groups in the expected direction? It does "
            "NOT establish that either group is actually correct. Recommend: review a sample of "
            "pending_review matches in this category via the dashboard before trusting any "
            "calibrated threshold."
        )

        calibration_row = MatchCalibrationRun(
            id=str(uuid.uuid4()),
            category_id=args.category_prefix,
            sample_size=len(rows),
            feature_importance=feature_importance,
            hard_constraints_proposed=hard_constraints_proposed,
            weights_proposed=weights_proposed,
            thresholds_proposed={"auto_match": 0.90, "deep_verification": 0.75, "ambiguous": 0.60, "reject": 0.60},
            notes=notes,
            profile_version_written=None,
        )
        session.add(calibration_row)

    logger.info("Calibration run saved (id=%s). %s", calibration_row.id, notes)


if __name__ == "__main__":
    main()
