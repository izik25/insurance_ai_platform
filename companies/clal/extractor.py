"""Clal identity-field extraction.

Unlike Migdal, there is nothing to extract from the file itself: the
search API already gives a clean appendix-number field per document (see
downloader.ClalDocumentRef). That value flows straight from the listing
into the database - this extractor exists only to satisfy the
BaseExtractor contract and returns nothing, on purpose.
"""

from __future__ import annotations

from pathlib import Path

from core.plugins.base import BaseExtractor


class ClalExtractor(BaseExtractor):
    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        return {}
