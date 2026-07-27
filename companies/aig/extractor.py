"""AIG identity-field extraction.

There is nothing to extract from the file itself: when an appendix number
is present at all, it's already parsed out of the link title in
downloader.py and flows straight into the database. This extractor exists
only to satisfy the BaseExtractor contract.
"""

from __future__ import annotations

from pathlib import Path

from core.plugins.base import BaseExtractor


class AigExtractor(BaseExtractor):
    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        return {}
