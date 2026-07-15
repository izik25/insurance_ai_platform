"""Extract identity fields for every downloaded Migdal document and persist
them to the database: company, filename, domain, and appendix number(s).

Re-fetches the archive listing (one HTTP GET) to recover the appendix name
and department per file — the downloader only writes PDFs to disk, it
doesn't persist that metadata anywhere.

Usage: python scripts/build_migdal_db.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.migdal import register  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.database.models import Company, Document  # noqa: E402
from core.database.session import init_db, session_scope  # noqa: E402
from core.exceptions import PdfProcessingError  # noqa: E402
from core.pdf_processing.document import PdfDocument  # noqa: E402
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
    plugin = registry.get("migdal")

    refs_by_filename = {ref.local_filename: ref for ref in plugin.downloader.list_documents()}

    migdal_dir = settings.raw_documents_dir / "migdal"
    files = sorted(migdal_dir.rglob("*.pdf"))
    if args.limit is not None:
        files = files[: args.limit]

    with session_scope() as session:
        session.merge(
            Company(id=plugin.config.company_id, display_name=plugin.config.display_name)
        )

    saved = 0
    failed = 0
    for index, file_path in enumerate(files, start=1):
        try:
            ref = refs_by_filename.get(file_path.name)
            text = plugin.parser.extract_text(file_path)
            fields = plugin.extractor.extract_fields(file_path, text)

            try:
                with PdfDocument(file_path) as doc:
                    pages_count = doc.page_count
            except PdfProcessingError:
                pages_count = None

            relative_path = file_path.relative_to(migdal_dir.parent)
            document = Document(
                id=f"{plugin.config.company_id}:{relative_path.as_posix()}",
                company_id=plugin.config.company_id,
                original_file_name=file_path.name,
                file_path=relative_path.as_posix(),
                domain=file_path.parent.name,
                appendix_number=fields.get("appendix_number") or [],
                appendix_name=ref.policy_name if ref else None,
                department_name=ref.department_name if ref else None,
                pages_count=pages_count,
                extraction_method="text" if text.strip() else "ocr",
            )
            with session_scope() as session:
                session.merge(document)
            saved += 1
        except Exception as exc:  # noqa: BLE001 - keep the batch going on any single-file failure
            failed += 1
            logger.warning("Failed to process %s: %s", file_path, exc)

        if index % 100 == 0:
            logger.info("Progress: %d/%d", index, len(files))

    logger.info("Done. saved=%d failed=%d total=%d", saved, failed, len(files))


if __name__ == "__main__":
    main()
