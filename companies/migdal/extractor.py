"""Migdal appendix-number extraction.

Migdal's saved filename doubles as a hint: downloader.py names files
`<media_folder_id>_<original_filename>`, and for most documents the
original filename is itself the appendix number (e.g. "101.pdf" for
"נספח 101" — confirmed by inspecting a real document). That hint is never
trusted blindly: it is cross-checked against the number(s) actually
printed on the page, read via the embedded text layer or, for scanned
documents, targeted OCR of the page-1 footer (see MigdalRules).
"""

from __future__ import annotations

from pathlib import Path

from companies.migdal.config import MigdalConfig
from companies.migdal.rules import MigdalRules
from core.exceptions import PdfProcessingError
from core.extraction.appendix_number import find_appendix_numbers
from core.ocr.engine import TesseractEngine
from core.ocr.regions import crop_region
from core.pdf_processing.document import PdfDocument
from core.plugins.base import BaseExtractor
from core.utils.logging import get_logger

logger = get_logger(__name__)


def _filename_hint(file_path: Path) -> str | None:
    """Return the appendix-number hint encoded in the filename, if any."""
    stem = file_path.stem
    if "_" not in stem:
        return None
    _, original_stem = stem.split("_", 1)
    return original_stem if original_stem.isdigit() else None


class MigdalExtractor(BaseExtractor):
    """Derives appendix number(s) for a Migdal document.

    `ocr_engine` is optional: without one, documents with no embedded text
    layer simply yield no OCR-derived numbers (still usable in tests, or
    for a dry run over documents expected to have real text layers).
    """

    def __init__(self, config: MigdalConfig, ocr_engine: TesseractEngine | None = None) -> None:
        super().__init__(config)
        self._ocr_engine = ocr_engine
        self._rules = MigdalRules(config)

    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        # Search page 1 first, same scope as the OCR crop — a document's
        # own identifying number lives in its footer, not in whichever
        # page happens to mention "נספח" while cross-referencing another
        # one. Searching the whole document first was tried and produced
        # false positives (e.g. picking up ['10', '2', '200'] from
        # internal cross-references in one real sample).
        page_one_text = self._get_page_one_text(file_path)
        found_numbers = find_appendix_numbers(page_one_text)
        if not found_numbers and not page_one_text.strip():
            found_numbers = find_appendix_numbers(self._ocr_first_page_footer(file_path))

        # Last resort: broaden to the whole document.
        if not found_numbers and text.strip():
            found_numbers = find_appendix_numbers(text)

        hint = _filename_hint(file_path)

        if hint and hint in found_numbers:
            numbers = found_numbers
        elif found_numbers:
            logger.info(
                "Filename hint %r for %s not confirmed by page content %r; trusting page content",
                hint,
                file_path,
                found_numbers,
            )
            numbers = found_numbers
        elif hint:
            logger.warning(
                "No appendix number found in content for %s; falling back to filename hint %r",
                file_path,
                hint,
            )
            numbers = [hint]
        else:
            numbers = []

        return {"appendix_number": numbers}

    def _get_page_one_text(self, file_path: Path) -> str:
        try:
            with PdfDocument(file_path) as doc:
                if doc.page_count == 0:
                    return ""
                return doc.extract_text(0)
        except PdfProcessingError as exc:
            logger.warning("Could not read page 1 of %s: %s", file_path, exc)
            return ""

    def _ocr_first_page_footer(self, file_path: Path) -> str:
        if self._ocr_engine is None:
            return ""

        try:
            with PdfDocument(file_path) as doc:
                if doc.page_count == 0:
                    return ""
                image = doc.render_page_to_image(0, dpi=300)
        except PdfProcessingError as exc:
            logger.warning("Could not render %s for OCR: %s", file_path, exc)
            return ""

        texts = []
        for region in self._rules.get_ocr_crop_regions(0):
            result = self._ocr_engine.run(crop_region(image, region))
            texts.append(result.text)
        return "\n".join(texts)
