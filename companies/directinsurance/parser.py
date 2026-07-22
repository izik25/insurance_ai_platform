"""Direct Insurance PDF text parsing (no OCR)."""

from __future__ import annotations

from pathlib import Path

from core.exceptions import PdfProcessingError
from core.pdf_processing.document import PdfDocument
from core.plugins.base import BaseParser
from core.utils.logging import get_logger

logger = get_logger(__name__)


class DirectInsuranceParser(BaseParser):
    """Extracts embedded PDF text via PyMuPDF.

    Not actually needed to identify a document - metadata comes from the
    search-API response (see downloader.py) - but implemented for the
    downstream structured-extraction/embedding pipeline, same as every
    other company.
    """

    def extract_text(self, file_path: Path) -> str:
        try:
            with PdfDocument(file_path) as doc:
                return doc.extract_all_text()
        except PdfProcessingError as exc:
            logger.warning("Could not extract text from %s: %s", file_path, exc)
            return ""
