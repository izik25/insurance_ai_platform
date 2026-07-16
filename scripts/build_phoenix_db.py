"""Persist Phoenix's downloaded health/life documents to the database.

Unlike Migdal, no PDF needs to be opened: the appendix number, title, and
edition all come straight from the archive listing (re-fetched here,
matched to files already on disk by filename).

Usage: python scripts/build_phoenix_db.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.phoenix import register  # noqa: E402
from companies.phoenix.downloader import PhoenixDownloader  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import Company, Document  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.plugins.registry import CompanyRegistry  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--limit", type=int, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    init_db()

    registry = CompanyRegistry()
    register(registry)
    plugin = registry.get("phoenix")
    assert isinstance(plugin.downloader, PhoenixDownloader)

    refs_by_filename = {ref.local_filename: ref for ref in plugin.downloader.list_documents()}

    phoenix_dir = settings.raw_documents_dir / "phoenix"
    files = sorted(phoenix_dir.rglob("*.pdf"))
    if args.limit is not None:
        files = files[: args.limit]

    with session_scope() as session:
        session.merge(
            Company(id=plugin.config.company_id, display_name=plugin.config.display_name)
        )

    saved = 0
    skipped = 0
    for file_path in files:
        ref = refs_by_filename.get(file_path.name)
        if ref is None:
            logger.warning("No listing metadata found for %s; skipping", file_path)
            skipped += 1
            continue

        relative_path = file_path.relative_to(phoenix_dir.parent)
        document = Document(
            id=f"{plugin.config.company_id}:{relative_path.as_posix()}",
            company_id=plugin.config.company_id,
            original_file_name=file_path.name,
            file_path=relative_path.as_posix(),
            domain=file_path.parent.name,
            appendix_number=[ref.appendix_number] if ref.appendix_number else [],
            appendix_name=ref.title,
            department_name=None,
            pages_count=None,
            extraction_method="manual",
        )
        with session_scope() as session:
            session.merge(document)
        saved += 1

    logger.info("Done. saved=%d skipped=%d total=%d", saved, skipped, len(files))


if __name__ == "__main__":
    main()
