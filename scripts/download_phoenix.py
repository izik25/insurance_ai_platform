"""Download Phoenix's health/life policy-terms archive.

Usage: python scripts/download_phoenix.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companies.phoenix import register  # noqa: E402
from core.config.settings import get_settings  # noqa: E402
from core.plugins.registry import CompanyRegistry  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)

    registry = CompanyRegistry()
    register(registry)
    plugin = registry.get("phoenix")

    destination = settings.raw_documents_dir / "phoenix"
    saved = plugin.downloader.download_all(destination, limit=args.limit)
    logger.info("Done. Saved %d files to %s", len(saved), destination)


if __name__ == "__main__":
    main()
