"""Hachshara appendix-number / document-code extraction.

Critical finding (confirmed against a real downloaded document,
media/3bdgqiqd/..._נספח_531_גילוי_נאות_102023.pdf): the page has a genuine
embedded text layer, but PyMuPDF's `get_text()` plain-text stream emits the
page-1 header line "מספר נספח 531" (visual reading order) as "531 נספח
מספר" (stream order) - digits before "נספח". The shared
`core.extraction.appendix_number.find_appendix_numbers` regex therefore
cannot match here even after accounting for word order, because the actual
phrasing also has "מספר" between "נספח" and the digits. Fixed in two parts:
`core.pdf_processing.reading_order.reconstruct_rtl_line_order` rebuilds
visual order from word bounding boxes, and `_find_appendix_numbers` below
tolerates the extra "מספר" token - and its abbreviated form "מס'", confirmed
on a life-domain document (media/d0od3kbm/..._נספח_662_...pdf), where
PyMuPDF tokenizes the apostrophe as its own word ("מס", "'", "662").

Not every document necessarily has a clean "נספח <n>" mention at all (many
are generic forms/questionnaires/brochures, or use a non-numeric appendix
code like "נספח ג-2") - a document simply gets `appendix_number: []` in that
case, same as other companies.

Second confirmed finding (life domain, "700-series" template - media ids
h3zbi5oj/ncufucns/nw2d5cge): some documents' header numeral is genuinely
corrupted in the text layer - e.g. rendering the page to an image shows
"700" but `get_text()` extracts "077" (verified visually, not just a
word-order artifact: this is a single already-adjacent token, and the two
strings aren't even the same multiset of digits). Filename-hint vs.
page-content precedence is therefore the reverse of Migdal's: every real
conflict found here turned out to be corrupted content with a correct
hint, never the other way around, so on a genuine conflict this extractor
trusts the filename hint (see extract_fields below).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from companies.hachshara.config import HachsharaConfig
from companies.hachshara.rules import HachsharaRules
from core.exceptions import PdfProcessingError
from core.ocr.engine import TesseractEngine
from core.ocr.regions import crop_region
from core.pdf_processing.document import PdfDocument
from core.pdf_processing.reading_order import reconstruct_rtl_line_order
from core.plugins.base import BaseExtractor
from core.utils.logging import get_logger

logger = get_logger(__name__)

_APPENDIX_MENTION = re.compile(
    r"\bנספח(?:ים)?\s+(?:(?:מספר|מס)\s*'?\s*)?(\d+(?:\s*(?:,|ו-?|-)\s*\d+)*)"
)
_DIGITS = re.compile(r"\d+")
_APPENDIX_KEYWORDS = {"נספח", "מספר", "מס"}
_HEADER_Y_FRACTION = 0.20  # top ~20% of page 1 (confirmed sample: header at y=90-127 of 842pt)

# "גרסה" (edition/version) followed by a MM/YYYY or MM.YYYY date, or the
# reverse - PyMuPDF's stream order for this RTL word+date pair flips
# between documents (confirmed live across a sample of real Hachshara
# PDFs: e.g. "10.2023 גרסה" in most, but "גרסה10/2014" - no space, word
# first - in others). No structured validity date exists on Hachshara's
# listing page for this (unlike Harel/Clal/Direct Insurance/Migdal), but
# this "גרסה" marker on page 1 (occasionally page 2) reliably does -
# confirmed on ~85% of a live sample; the rest have no version marker at
# all and are left with no marketing dates (same "no signal -> active"
# default as every other company, an accepted gap like AIG's).
_VERSION_PATTERN = re.compile(
    r"(?:(?P<d1>\d{1,2}[./]\d{4})\s*גרסה|גרסה\s*(?P<d2>\d{1,2}[./]\d{4}))"
)


def find_version_date(text: str) -> date | None:
    """Extract the "גרסה MM/YYYY" (or MM.YYYY, either word order) edition
    marker from a document's text - see _VERSION_PATTERN's comment."""
    match = _VERSION_PATTERN.search(text)
    if match is None:
        return None
    raw = match.group("d1") or match.group("d2")
    month_str, year_str = re.split(r"[./]", raw)
    try:
        return date(int(year_str), int(month_str), 1)
    except ValueError:
        return None


def _find_appendix_numbers(text: str) -> list[str]:
    """Hachshara-local pattern: a strict superset of the shared
    find_appendix_numbers - tolerates an optional "מספר"/"מס'" between
    "נספח" and the digits (e.g. "נספח מספר 531", "נספח מס' 662"), confirmed
    necessary on real content from both domains.
    """
    numbers: list[str] = []
    for mention in _APPENDIX_MENTION.finditer(text):
        for number in _DIGITS.findall(mention.group(1)):
            if number not in numbers:
                numbers.append(number)
    return numbers


def _filename_hint(file_path: Path) -> str | None:
    """A digit token immediately following "נספח"/"מספר"/"מס" (allowing an
    attached one-letter prefix like "ל"/"ה"/"ב" - e.g. "לנספח_730", found
    live on media/0v2dgudi) in the saved filename. Deliberately narrower
    than "the whole stem is the number": Hachshara's saved filenames
    (media_id + origin filename) routinely embed OTHER numeric tokens too
    (a CMS timestamp prefix, a date suffix like "_102023") that must not be
    mistaken for the appendix number - e.g.
    "3bdgqiqd_1765214414_נספח_531_גילוי_נאות_102023" must yield "531", not
    "1765214414" or "102023"."""
    tokens = re.split(r"[_\-]+", file_path.stem)
    for previous, token in zip(tokens, tokens[1:], strict=False):
        if not token.isdigit():
            continue
        # "מס"/"מספר" stay exact-match: both are short enough that
        # tolerating an attached prefix (endswith) risks false positives
        # from unrelated words (e.g. "המס" - "the tax"). "נספח" is long and
        # distinctive enough that a prefix-tolerant endswith is safe, and
        # is the one confirmed live (media/0v2dgudi's "לנספח_730").
        if previous in _APPENDIX_KEYWORDS or previous.endswith("נספח"):
            return token
    return None


class HachsharaExtractor(BaseExtractor):
    """Derives appendix number(s)/document code for a Hachshara document.

    `ocr_engine` is optional: without one, documents with no embedded text
    layer simply yield no OCR-derived numbers (still usable in tests, or for
    a dry run over documents expected to have real text layers).
    """

    def __init__(self, config: HachsharaConfig, ocr_engine: TesseractEngine | None = None) -> None:
        super().__init__(config)
        self._ocr_engine = ocr_engine
        self._rules = HachsharaRules(config)

    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        page_one_text = self._get_page_one_text(file_path)
        found_numbers = _find_appendix_numbers(page_one_text)

        if not found_numbers:
            # get_text()'s plain-text stream can scramble word order on RTL
            # lines mixing Hebrew and digits (confirmed live) - reconstruct
            # visual order from word bounding boxes before giving up.
            found_numbers = _find_appendix_numbers(self._reconstructed_header(file_path))

        if not found_numbers and not page_one_text.strip():
            found_numbers = _find_appendix_numbers(self._ocr_header(file_path))

        # Last resort: broaden to the whole document.
        if not found_numbers and text.strip():
            found_numbers = _find_appendix_numbers(text)

        hint = _filename_hint(file_path)

        if hint and hint in found_numbers:
            numbers = found_numbers
        elif hint and found_numbers:
            # Confirmed live (media/h3zbi5oj, ncufucns, nw2d5cge - a
            # "700-series" life-domain template): these documents visually
            # render "700"/"702"/"704" in their header (verified by
            # rendering the page to an image) but PyMuPDF's text layer
            # extracts "077"/"207"/"407" - a font/ToUnicode-mapping defect
            # in the source PDF, not a reading-order problem. Every real
            # hint/content conflict found in this corpus turned out to be
            # corrupted content, never a stale hint - so, unlike Migdal
            # (whose filenames are the less trustworthy side), Hachshara
            # trusts the filename hint over conflicting page content.
            logger.warning(
                "Filename hint %r for %s conflicts with page content %r; "
                "trusting filename hint (suspected font/encoding corruption in the PDF)",
                hint,
                file_path,
                found_numbers,
            )
            numbers = [hint]
        elif found_numbers:
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

    def _reconstructed_header(self, file_path: Path) -> str:
        try:
            with PdfDocument(file_path) as doc:
                if doc.page_count == 0:
                    return ""
                words = doc.extract_words(0)
                if not words:
                    return ""
                _, height = doc.page_size(0)
        except PdfProcessingError as exc:
            logger.warning("Could not read page 1 words of %s: %s", file_path, exc)
            return ""

        header_words = [word for word in words if word[1] <= height * _HEADER_Y_FRACTION]
        return reconstruct_rtl_line_order(header_words)

    def _ocr_header(self, file_path: Path) -> str:
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
