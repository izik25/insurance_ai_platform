"""Migdal PDF text parsing (no OCR)."""

from __future__ import annotations

from pathlib import Path

from core.exceptions import PdfProcessingError
from core.pdf_processing.document import PdfDocument
from core.plugins.base import BaseParser
from core.utils.logging import get_logger

logger = get_logger(__name__)


class MigdalParser(BaseParser):
    """Extracts embedded PDF text via PyMuPDF; returns '' for scanned documents."""

    def extract_text(self, file_path: Path) -> str:
        try:
            with PdfDocument(file_path) as doc:
                return doc.extract_all_text()
        except PdfProcessingError as exc:
            logger.warning("Could not extract text from %s: %s", file_path, exc)
            return ""
