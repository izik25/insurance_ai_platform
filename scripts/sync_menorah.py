"""Fetch Menorah's health/life listing once, then download and populate the
DB from that same listing (same pattern as sync_phoenix.py/sync_clal.py).

The listing is cached to disk (_listing_cache.json next to the downloaded
files) so re-running this script to pick up download retries or DB fixes
doesn't mean re-running the (slow, deliberately-paced - see
MenorahConfig.listing_delay_seconds) search. Pass --refresh-listing to
force a fresh search (e.g. to pick up newly published documents).

Usage: python scripts/sync_menorah.py [--limit N] [--refresh-listing]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.menorah import register  # noqa: E402
from companies.menorah.downloader import MenorahDocumentRef, MenorahDownloader  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import Company, Document  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.plugins.registry import CompanyRegistry  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _load_cached_listing(cache_path: Path) -> list[MenorahDocumentRef] | None:
    if not cache_path.is_file():
        return None
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    return [
        MenorahDocumentRef(
            **{
                **entry,
                "marketing_start_date": (
                    date.fromisoformat(entry["marketing_start_date"])
                    if entry.get("marketing_start_date")
                    else None
                ),
                "marketing_end_date": (
                    date.fromisoformat(entry["marketing_end_date"])
                    if entry.get("marketing_end_date")
                    else None
                ),
            }
        )
        for entry in raw
    ]


def _save_listing_cache(cache_path: Path, refs: list[MenorahDocumentRef]) -> None:
    payload = [
        {
            "domain": ref.domain,
            "title": ref.title,
            "appendix_numbers": ref.appendix_numbers,
            "download_url": ref.download_url,
            "marketing_start_date": (
                ref.marketing_start_date.isoformat() if ref.marketing_start_date else None
            ),
            "marketing_end_date": (
                ref.marketing_end_date.isoformat() if ref.marketing_end_date else None
            ),
        }
        for ref in refs
    ]
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    arg_parser.add_argument(
        "--refresh-listing", action="store_true", help="Force a fresh search of the archive."
    )
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    registry = CompanyRegistry()
    register(registry)
    plugin = registry.get("menorah")
    assert isinstance(plugin.downloader, MenorahDownloader)

    destination = settings.raw_documents_dir / "menorah"
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
            appendix_number=ref.appendix_numbers,
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
