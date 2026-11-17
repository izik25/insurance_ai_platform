"""Give every AIG document its internal "AIG-..." identifier
(companies/aig/identifiers.py) as its appendix number.

AIG publishes no appendix numbers; what the DB held for AIG was LLM-backfill
guesses ("1", "1,2", "4910001" - the last shared by unrelated products, so
it could never identify one document). Every AIG document's appendix_number
is replaced by one unique internal identifier; the previous values are
written to --audit-file so nothing is lost.

Deterministic order (domain, title, id) decides which of two same-title
documents gets the "-2" suffix, so a re-run reproduces the same mapping.

Usage: python scripts/assign_aig_internal_ids.py [--apply] [--audit-file PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.aig.identifiers import unique_internal_id  # noqa: E402
from core.database.models import Document  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write to the DB (default: dry run)")
    parser.add_argument(
        "--audit-file", type=Path, default=Path("data/processed/aig_internal_ids.json")
    )
    args = parser.parse_args()
    init_db()

    taken: set[str] = set()
    audit = []
    with session_scope() as session:
        documents = (
            session.query(Document)
            .filter(Document.company_id == "aig")
            .order_by(Document.domain, Document.appendix_name, Document.id)
            .all()
        )
        for document in documents:
            new_id = unique_internal_id(document.appendix_name or Path(document.file_path).stem, taken)
            audit.append(
                {
                    "document_id": document.id,
                    "title": document.appendix_name,
                    "previous_appendix_number": list(document.appendix_number or []),
                    "internal_id": new_id,
                }
            )
            print(f"{new_id:50s} <- {list(document.appendix_number or [])} | {(document.appendix_name or '')[:60]}")
            if args.apply:
                document.appendix_number = [new_id]
        if not args.apply:
            session.rollback()

    args.audit_file.parent.mkdir(parents=True, exist_ok=True)
    args.audit_file.write_text(json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {len(audit)} AIG documents, "
          f"{len(taken)} unique ids; audit -> {args.audit_file}")


if __name__ == "__main__":
    main()
