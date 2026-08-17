"""Fetch Hachshara's file-finder listing once, then download and populate the
DB from that same listing (same pattern as sync_directinsurance.py).

Unlike Direct Insurance/Menorah, this company's extractor does real,
zero-API-cost work (regex + PDF geometry, see companies/hachshara/extractor.py)
to read the appendix number/document code off each document's own page 1 -
so this script calls it per file, same as scripts/build_migdal_db.py, rather
than trusting listing metadata alone.

Unlike Harel/Clal/Direct Insurance/Migdal, this company's listing page
carries no structured validity-date field at all (confirmed live
2026-08-10) - so marketing_start_date/marketing_end_date are instead
derived, same spirit as Phoenix's `edition`, from each document's own
`companies/hachshara/extractor.find_version_date` (a "גרסה MM/YYYY" marker
on page 1/2) grouped by appendix number: the newest-versioned document per
appendix number is active (marketing_end_date=None), older ones get
marketing_end_date set to just before the next version in their group.
Documents with no parseable version marker, or no appendix number to group
by, are left with no marketing dates at all (active by default, an
accepted gap - not every document carries this marker).

The listing is cached to disk (_listing_cache.json next to the downloaded
files) so re-running this script to pick up download retries or DB fixes
doesn't mean re-fetching every listing page. Pass --refresh-listing to force
a fresh fetch (e.g. to pick up newly published documents).

Usage: python scripts/sync_hachshara.py [--limit N] [--refresh-listing]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.hachshara import register  # noqa: E402
from companies.hachshara.downloader import (  # noqa: E402
    HachsharaDocumentRef,
    HachsharaDownloader,
)
from companies.hachshara.extractor import find_version_date  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import Company, Document  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import PdfProcessingError  # noqa: E402
from core.pdf_processing.document import PdfDocument  # noqa: E402
from core.plugins.registry import CompanyRegistry  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


@dataclass
class _PendingDocument:
    """One file's worth of Document fields, collected before the
    version-date grouping pass below can fill in marketing_start_date/
    marketing_end_date (which needs every file's appendix_number+version_date
    up front, not just this one file's)."""

    document_id: str
    file_path: Path
    relative_path: str
    domain: str
    appendix_number: list[str]
    appendix_name: str | None
    pages_count: int | None
    extraction_method: str
    version_date: date | None
    marketing_start_date: date | None = None
    marketing_end_date: date | None = None


def _apply_marketing_dates(pending: list[_PendingDocument]) -> None:
    """Group by (domain, appendix_number) and derive marketing_start_date/
    marketing_end_date from each group's version_dates in place - same
    "newest version is active, older ones end just before the next" rule as
    Phoenix's with_marketing_dates, see this module's docstring."""
    groups: dict[tuple[str, tuple[str, ...]], list[_PendingDocument]] = {}
    for doc in pending:
        if not doc.appendix_number or doc.version_date is None:
            continue  # no signal - stays active by default, same as elsewhere
        groups.setdefault((doc.domain, tuple(sorted(doc.appendix_number))), []).append(doc)

    for group in groups.values():
        distinct_dates = sorted({doc.version_date for doc in group})
        next_date_by_date = dict(zip(distinct_dates, distinct_dates[1:] + [None], strict=False))
        for doc in group:
            doc.marketing_start_date = doc.version_date
            next_date = next_date_by_date[doc.version_date]
            doc.marketing_end_date = next_date - timedelta(days=1) if next_date else None


def _load_cached_listing(cache_path: Path) -> list[HachsharaDocumentRef] | None:
    if not cache_path.is_file():
        return None
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    return [HachsharaDocumentRef(**entry) for entry in raw]


def _save_listing_cache(cache_path: Path, refs: list[HachsharaDocumentRef]) -> None:
    payload = [
        {
            "domain": ref.domain,
            "media_id": ref.media_id,
            "title": ref.title,
            "appendix_numbers": ref.appendix_numbers,
            "download_url": ref.download_url,
        }
        for ref in refs
    ]
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument(
        "--refresh-listing", action="store_true", help="Force a fresh fetch of the archive."
    )
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    registry = CompanyRegistry()
    register(registry)
    plugin = registry.get("hachshara")
    assert isinstance(plugin.downloader, HachsharaDownloader)

    destination = settings.raw_documents_dir / "hachshara"
    cache_path = destination / "_listing_cache.json"

    refs = None if args.refresh_listing else _load_cached_listing(cache_path)
    if refs is not None:
        logger.info("Using cached listing: %d documents from %s", len(refs), cache_path)
    else:
        refs = plugin.downloader.list_documents()
        destination.mkdir(parents=True, exist_ok=True)
        _save_listing_cache(cache_path, refs)
        logger.info("Listed %d documents (cached to %s)", len(refs), cache_path)

    saved_paths = plugin.downloader.download_all(destination, limit=args.limit, refs=refs)
    logger.info("Downloaded/verified %d files on disk", len(saved_paths))

    refs_by_filename = {ref.local_filename: ref for ref in refs}
    with session_scope() as session:
        session.merge(
            Company(id=plugin.config.company_id, display_name=plugin.config.display_name)
        )

    files = sorted(destination.rglob("*.pdf"))
    if args.limit is not None:
        files = files[: args.limit]

    skipped = 0
    pending: list[_PendingDocument] = []
    for file_path in files:
        ref = refs_by_filename.get(file_path.name)
        if ref is None:
            logger.warning("No listing metadata found for %s; skipping", file_path)
            skipped += 1
            continue

        text = plugin.parser.extract_text(file_path)
        fields = plugin.extractor.extract_fields(file_path, text)

        try:
            with PdfDocument(file_path) as doc:
                pages_count = doc.page_count
        except PdfProcessingError:
            pages_count = None

        relative_path = file_path.relative_to(destination.parent)
        document_id = f"{plugin.config.company_id}:{relative_path.as_posix()}"
        with session_scope() as session:
            # The extractor's own regex/geometry read of page 1 is the
            # source of truth here (see extractor.py) - but a re-sync that
            # somehow finds nothing this time (e.g. a transient render
            # issue) shouldn't clobber a previously-found value.
            existing = session.get(Document, document_id)
            appendix_number = fields.get("appendix_number") or []
            if not appendix_number and existing is not None and existing.appendix_number:
                appendix_number = existing.appendix_number

        pending.append(
            _PendingDocument(
                document_id=document_id,
                file_path=file_path,
                relative_path=relative_path.as_posix(),
                domain=file_path.parent.name,
                appendix_number=appendix_number,
                appendix_name=ref.title or None,
                pages_count=pages_count,
                extraction_method="text" if text.strip() else "ocr",
                version_date=find_version_date(text),
            )
        )

    _apply_marketing_dates(pending)

    saved = 0
    for doc in pending:
        document = Document(
            id=doc.document_id,
            company_id=plugin.config.company_id,
            original_file_name=doc.file_path.name,
            file_path=doc.relative_path,
            domain=doc.domain,
            appendix_number=doc.appendix_number,
            appendix_name=doc.appendix_name,
            department_name=None,
            marketing_start_date=doc.marketing_start_date,
            marketing_end_date=doc.marketing_end_date,
            pages_count=doc.pages_count,
            extraction_method=doc.extraction_method,
        )
        with session_scope() as session:
            session.merge(document)
        saved += 1

    logger.info("DB done. saved=%d skipped=%d total=%d", saved, skipped, len(files))


if __name__ == "__main__":
    main()
