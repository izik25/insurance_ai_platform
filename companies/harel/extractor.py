"""Harel identity-field extraction.

Nothing to extract from the file itself: the archive results table already
gives the appendix number (the "מספר נספח" column, see
downloader.HarelDocumentRef) straight from the site's own metadata - same
situation as Phoenix/Clal/Menorah. This extractor exists only to satisfy
the BaseExtractor contract and returns nothing, on purpose.
"""

from __future__ import annotations

from pathlib import Path

from core.plugins.base import BaseExtractor


class HarelExtractor(BaseExtractor):
    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        return {}
