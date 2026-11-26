"""Rebuild the DATA blob of the client review page (frontend/public/review.html)
from the database, for the same documents the page already lists.

The page embeds one JSON object (`const DATA = {...};`): per document its
question-bank answers, fingerprint and canonical codes, plus every saved
document_matches row between two listed documents (the judge's verdict,
reasoning and material differences). After re-running the pipeline on the
review sample, this script refreshes that blob in place - document order
(and so every `i` index the saved reviewer marks refer to) is kept.

Usage: python scripts/export_review_data.py PAGE.html [PAGE2.html ...] [--check]
       --check: only report what would change, write nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core.database.models import (  # noqa: E402
    DocumentCanonicalCode,
    DocumentFingerprint,
    DocumentMatch,
    DocumentQuestionAnswer,
)
from core.database.session import init_db, session_scope  # noqa: E402

_DATA_LINE = re.compile(r"^const DATA = (\{.*\});\s*$", re.M)
_FP_KEYS = (
    "waiting_period_days", "qualifying_period_days", "survival_period_days", "min_entry_age",
    "max_entry_age", "termination_age", "benefit_type", "benefit_amount_max", "maximum_benefit",
    "deductible_amount", "target_population",
)
_FP_FULL_KEYS = (
    "main_category", "coverage_family", "coverage_subtype", "benefit_model", "target_population",
    "waiting_period_days", "waiting_period_raw", "qualifying_period_days", "qualifying_period_raw",
    "survival_period_days", "survival_period_raw", "min_entry_age", "max_entry_age", "termination_age",
    "age_raw", "benefit_type", "benefit_amount_min", "benefit_amount_max", "benefit_amount_currency",
    "maximum_benefit", "benefit_percentage", "deductible_amount", "amount_raw", "covered_event_count",
    "major_exclusion_count", "special_condition_count",
)


def _num(v):
    return round(v, 3) if isinstance(v, float) else v


def rebuild(data: dict) -> dict:
    docs = data["docs"]
    ids = [d["id"] for d in docs]
    index = {doc_id: i for i, doc_id in enumerate(ids)}
    with session_scope() as s:
        answers: dict[str, dict] = {i: {} for i in ids}
        for a in s.scalars(select(DocumentQuestionAnswer).where(DocumentQuestionAnswer.document_id.in_(ids))):
            answers[a.document_id][a.question_id] = [a.status, a.answer_text, a.evidence_text, a.evidence_page]
        fps = {f.document_id: f for f in s.scalars(select(DocumentFingerprint).where(DocumentFingerprint.document_id.in_(ids)))}
        codes: dict[str, list] = {i: [] for i in ids}
        for c in s.scalars(
            select(DocumentCanonicalCode)
            .where(DocumentCanonicalCode.document_id.in_(ids))
            .order_by(DocumentCanonicalCode.code_category, DocumentCanonicalCode.code)
        ):
            codes[c.document_id].append([c.code_category, c.code])
        matches = list(s.scalars(
            select(DocumentMatch).where(DocumentMatch.document_id.in_(ids), DocumentMatch.matched_document_id.in_(ids))
        ))
        for d in docs:
            fp = fps.get(d["id"])
            if fp is None:
                raise SystemExit(f"no fingerprint for {d['id']}")
            d["fp"] = {k: _num(getattr(fp, k)) for k in _FP_KEYS}
            d["fpFull"] = {k: _num(getattr(fp, k)) for k in _FP_FULL_KEYS}
            d["adj"] = list((fp.raw_features or {}).get("sanity_adjustments") or [])
            d["cov"] = (fp.raw_features or {}).get("is_coverage_document", True)
            d["qa"] = dict(sorted(answers[d["id"]].items()))
            d["codes"] = codes[d["id"]]
        outcomes = []
        for m in sorted(matches, key=lambda m: (index[m.document_id], index[m.matched_document_id])):
            judge = (m.score_breakdown or {}).get("judge") or {}
            outcomes.append({
                "f": index[m.document_id], "t": index[m.matched_document_id],
                "s": round(m.final_score or m.similarity_score or 0, 3), "st": m.status,
                "v": m.auditor_verdict, "r": m.auditor_reasoning,
                "diffs": m.material_differences or [],
                "ev": judge.get("core_insured_event"), "pay": judge.get("core_payment_mechanism"),
            })
        data["outcomes"] = outcomes
    return data


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pages", nargs="+", type=Path)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    init_db()
    for page in args.pages:
        html = page.read_text(encoding="utf-8")
        m = _DATA_LINE.search(html)
        if not m:
            raise SystemExit(f"{page}: no `const DATA = ...;` line")
        old = json.loads(m.group(1))
        new = rebuild(json.loads(m.group(1)))
        changed_docs = [d["id"] for d, o in zip(new["docs"], old["docs"]) if d != o]
        pairs_old = {(o["f"], o["t"]) for o in old["outcomes"]}
        pairs_new = {(o["f"], o["t"]) for o in new["outcomes"]}
        print(f"{page}: {len(changed_docs)}/{len(new['docs'])} documents changed; "
              f"outcomes {len(pairs_old)} -> {len(pairs_new)} "
              f"(+{len(pairs_new - pairs_old)} new, -{len(pairs_old - pairs_new)} gone)")
        if not args.check:
            blob = json.dumps(new, ensure_ascii=False, separators=(", ", ": "))
            html = html[: m.start(1)] + blob + html[m.end(1):]
            page.write_text(html, encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
