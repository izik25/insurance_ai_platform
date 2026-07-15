"""Migdal identity-field extraction.

Minimal placeholder satisfying the BaseExtractor contract. Real policy /
appendix number extraction (text-based, falling back to OCR) lands in
Stage 3.
"""

from __future__ import annotations

from core.plugins.base import BaseExtractor


class MigdalExtractor(BaseExtractor):
    """Stage 3 will replace this with real field extraction."""

    def extract_fields(self, text: str) -> dict[str, str | None]:
        return {}
