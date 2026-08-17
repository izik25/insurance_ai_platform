"""Fetch Phoenix's health/life listing once, then download and populate the
DB from that same listing.

download_phoenix.py and build_phoenix_db.py each fetch the listing
independently, which means running both means paying for the (slow,
polite-paced) archive listing twice. This script fetches it once and
reuses it for both steps - the considerate way to run a full sync.

The listing is also cached to disk (_listing_cache.json next to the
downloaded files): a full crawl takes roughly an hour against the live
site, and re-running this script to pick up download retries or DB fixes
shouldn't mean re-crawling the whole archive again. Pass --refresh-listing
to force a fresh crawl (e.g. to pick up newly published documents).

Usage: python scripts/sync_phoenix.py [--limit N] [--refresh-listing]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.phoenix import register  # noqa: E402
from companies.phoenix.downloader import (  # noqa: E402
    PhoenixDocumentRef,
    PhoenixDownloader,
    with_marketing_dates,
)
from core.config.settings import get_settings  # noqa: E402
from core.database.models import Company, Document  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.plugins.registry import CompanyRegistry  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _load_cached_listing(cache_path: Path) -> list[PhoenixDocumentRef] | None:
    if not cache_path.is_file():
        return None
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    return [PhoenixDocumentRef(**entry) for entry in raw]


def _save_listing_cache(cache_path: Path, refs: list[PhoenixDocumentRef]) -> None:
    payload = [
        {
            "domain": ref.domain,
            "title": ref.title,
            "appendix_number": ref.appendix_number,
            "edition": ref.edition,
            "download_url": ref.download_url,
        }
        for ref in refs
    ]
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument(
        "--refresh-listing", action="store_true", help="Force a fresh crawl of the archive."
    )
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    registry = CompanyRegistry()
    register(registry)
    plugin = registry.get("phoenix")
    assert isinstance(plugin.downloader, PhoenixDownloader)

    destination = settings.raw_documents_dir / "phoenix"
    cache_path = destination / "_listing_cache.json"

    refs = None if args.refresh_listing else _load_cached_listing(cache_path)
    if refs is not None:
        logger.info("Using cached listing: %d documents from %s", len(refs), cache_path)
    else:
        refs = plugin.downloader.list_documents()
        destination.mkdir(parents=True, exist_ok=True)
        _save_listing_cache(cache_path, refs)
        logger.info("Listed %d documents (cached to %s)", len(refs), cache_path)

    # Derived from `edition` here rather than persisted in the cache - see
    # companies/phoenix/downloader.py's module docstring - so this is always
    # freshly (re)computed, cached listing or not.
    refs = with_marketing_dates(refs)

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

    saved = 0
    skipped = 0
    for file_path in files:
        ref = refs_by_filename.get(file_path.name)
        if ref is None:
            logger.warning("No listing metadata found for %s; skipping", file_path)
            skipped += 1
            continue

        relative_path = file_path.relative_to(destination.parent)
        document = Document(
            id=f"{plugin.config.company_id}:{relative_path.as_posix()}",
            company_id=plugin.config.company_id,
            original_file_name=file_path.name,
            file_path=relative_path.as_posix(),
            domain=file_path.parent.name,
            appendix_number=[ref.appendix_number] if ref.appendix_number else [],
            appendix_name=ref.title,
            department_name=None,
            marketing_start_date=ref.marketing_start_date,
            marketing_end_date=ref.marketing_end_date,
            pages_count=None,
            extraction_method="manual",
        )
        with session_scope() as session:
            session.merge(document)
        saved += 1

    logger.info("DB done. saved=%d skipped=%d total=%d", saved, skipped, len(files))


if __name__ == "__main__":
    main()
