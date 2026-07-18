"""Full-document text extraction with an OCR fallback, for any company's PDF.

Unlike `companies/migdal/extractor.py` (which only reads page 1's footer to
find an appendix number), this pulls the *entire* document's text - needed
as input to the structured-field LLM extraction, which has to see the whole
policy appendix, not just its first page.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.models.enums import ExtractionMethod
from core.ocr.engine import TesseractEngine
from core.pdf_processing.document import PdfDocument
from core.utils.logging import get_logger

logger = get_logger(__name__)

_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


def clean_text(raw_text: str) -> str:
    """Normalize whitespace without touching real content.

    OCR and PDF text extraction both tend to leave runs of spaces/tabs and
    long stretches of blank lines; collapsing those keeps the text compact
    for the LLM prompt without altering any actual words.
    """
    collapsed = _WHITESPACE_RUN.sub(" ", raw_text)
    collapsed = _BLANK_LINE_RUN.sub("\n\n", collapsed)
    return "\n".join(line.strip() for line in collapsed.splitlines()).strip()


def get_document_text(
    pdf_path: Path, ocr_engine: TesseractEngine
) -> tuple[str, ExtractionMethod]:
    """Return a document's full text, using OCR only for pages that need it.

    Each page is handled independently: pages with a usable embedded text
    layer are read directly (fast, exact); pages without one (scans) are
    rendered to an image and OCR'd. The reported method reflects whichever
    path did the heavier lifting - OCR if any page needed it, else TEXT.
    """
    pages: list[str] = []
    used_ocr = False

    with PdfDocument(pdf_path) as doc:
        for page_index in range(doc.page_count):
            if doc.has_text_layer(page_index):
                pages.append(doc.extract_text(page_index))
                continue

            used_ocr = True
            image = doc.render_page_to_image(page_index)
            result = ocr_engine.run(image)
            pages.append(result.text)

    method = ExtractionMethod.OCR if used_ocr else ExtractionMethod.TEXT
    return clean_text("\n".join(pages)), method
