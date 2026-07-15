"""Migdal PDF text parsing.

Minimal placeholder satisfying the BaseParser contract. Real embedded-text
extraction (via core.pdf_processing, PyMuPDF) lands in Stage 3.
"""

from __future__ import annotations

from pathlib import Path

from core.plugins.base import BaseParser


class MigdalParser(BaseParser):
    """Stage 3 will replace this with real PyMuPDF-based text extraction."""

    def extract_text(self, file_path: Path) -> str:
        return ""
