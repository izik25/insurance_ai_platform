"""Shared CLI options for running a pipeline stage on an explicit set of
documents (e.g. a review sample) instead of "everything pending".

--document-ids / --document-ids-file restrict the run to exactly those
document ids; --force re-processes them even when document_pipeline_status
says they're already current at this stage's version.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def add_document_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--document-ids", type=str, default=None, help="Comma-separated document ids"
    )
    parser.add_argument(
        "--document-ids-file",
        type=Path,
        default=None,
        help="Text file with one document id per line",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process selected documents even if already current",
    )


def read_document_ids(args: argparse.Namespace) -> set[str] | None:
    """None means "no restriction" (the stage's normal pending logic)."""
    ids: set[str] = set()
    if args.document_ids:
        ids.update(i.strip() for i in args.document_ids.split(",") if i.strip())
    if args.document_ids_file:
        lines = args.document_ids_file.read_text(encoding="utf-8").splitlines()
        ids.update(line.strip() for line in lines if line.strip())
    if args.force and not ids:
        raise SystemExit("--force requires --document-ids or --document-ids-file")
    return ids or None
