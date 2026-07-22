"""Menorah identity-field extraction.

Unlike Migdal, there is nothing to extract from the file itself: the
search API's response already gives the appendix number(s) embedded in
its policyHeader/tags (see downloader.MenorahDocumentRef, parsed via the
shared core.extraction.appendix_number helper). That value flows straight
from the listing into the database - this extractor exists only to
satisfy the BaseExtractor contract and returns nothing, on purpose.
"""

from __future__ import annotations

from pathlib import Path

from core.plugins.base import BaseExtractor


class MenorahExtractor(BaseExtractor):
    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        return {}
