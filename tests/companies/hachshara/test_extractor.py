from __future__ import annotations

from pathlib import Path

import fitz
import numpy as np

from companies.hachshara.config import HachsharaConfig
from companies.hachshara.extractor import HachsharaExtractor
from companies.hachshara.parser import HachsharaParser
from core.ocr.engine import OcrResult

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _StubOcrEngine:
    """Returns a fixed OCR result regardless of the image it's given."""

    def __init__(self, text: str) -> None:
        self._text = text

    def run(self, image: np.ndarray, *, preprocess: bool = False) -> OcrResult:
        return OcrResult(text=self._text, confidence=0.9)


def _make_blank_pdf(tmp_path: Path, name: str) -> Path:
    """A PDF with no embedded text layer (simulates a scanned document)."""
    doc = fitz.open()
    doc.new_page()
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def test_filename_hint_confirmed_by_page_text(tmp_path: Path) -> None:
    file_path = tmp_path / "3bdgqiqd_1765214414_נספח_531_גילוי_נאות_102023.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "...\nנספח מספר 531\n")

    assert fields["appendix_number"] == ["531"]


def test_filename_hint_trusted_over_conflicting_page_content(tmp_path: Path) -> None:
    """Regression test for a confirmed real defect (media/h3zbi5oj,
    ncufucns, nw2d5cge): these documents visually render "700"/"702"/"704"
    in their header (verified by rendering the page to an image) but the
    PDF's text layer extracts a corrupted value ("077"/"207"/"407") - a
    font/ToUnicode-mapping bug in the source PDF, not a reading-order
    issue. Every real hint/content conflict found in this corpus was this
    kind of corruption with a correct hint, so on conflict the hint wins -
    the opposite precedence from Migdal, which is deliberate (see
    extractor.py's module docstring)."""
    file_path = tmp_path / "h3zbi5oj_1764674821_נספח_700_גילוי_נאות.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "...\nנספח077\n")

    assert fields["appendix_number"] == ["700"]


def test_falls_back_to_filename_hint_when_no_content_found(tmp_path: Path) -> None:
    file_path = tmp_path / "3bdgqiqd_1765214414_נספח_531_גילוי_נאות_102023.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "some unrelated text with no mention")

    assert fields["appendix_number"] == ["531"]


def test_filename_hint_skips_cms_timestamp_and_date_tokens(tmp_path: Path) -> None:
    """The saved filename embeds a CMS timestamp prefix (1765214414) and a
    version-date suffix (102023) in addition to the real appendix number
    (531) - the hint must pick out 531, not either of the other numbers."""
    file_path = tmp_path / "3bdgqiqd_1765214414_נספח_531_גילוי_נאות_102023.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "no mention of any number here")

    assert fields["appendix_number"] == ["531"]


def test_filename_hint_recognized_with_attached_prefix_letter(tmp_path: Path) -> None:
    """Regression test for media/0v2dgudi: the filename token before the
    number is "לנספח" ("to/for the appendix"), not the exact word "נספח",
    but the hint must still be found - otherwise this document falls
    through to the same font-corruption bug as the 700-series case above
    with no hint available to override it (real observed corrupted content
    for this exact document was "037" instead of the true "730")."""
    file_path = tmp_path / "0v2dgudi_1765215499_גילוי_נאות_לנספח_730_הכי_להכנסה.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "גלוי נאות\nל\nנספח 037\n")

    assert fields["appendix_number"] == ["730"]


def test_no_hint_no_content_yields_empty(tmp_path: Path) -> None:
    file_path = tmp_path / "3bdgqiqd_בריאות-למשפחה.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "some unrelated text")

    assert fields["appendix_number"] == []


def test_ocr_fallback_used_when_no_embedded_text(tmp_path: Path) -> None:
    file_path = _make_blank_pdf(tmp_path, "3bdgqiqd_1765214414_נספח_531.pdf")
    ocr_engine = _StubOcrEngine(text="שורת רעש\nנספח 531")
    extractor = HachsharaExtractor(HachsharaConfig(), ocr_engine=ocr_engine)

    fields = extractor.extract_fields(file_path, "")  # no embedded text -> triggers OCR

    assert fields["appendix_number"] == ["531"]


def test_no_ocr_engine_and_no_embedded_text_falls_back_to_hint(tmp_path: Path) -> None:
    file_path = _make_blank_pdf(tmp_path, "3bdgqiqd_1765214414_נספח_531.pdf")
    extractor = HachsharaExtractor(HachsharaConfig(), ocr_engine=None)

    fields = extractor.extract_fields(file_path, "")

    assert fields["appendix_number"] == ["531"]


def test_ignores_clause_number_after_word_containing_nespach_as_prefix(tmp_path: Path) -> None:
    """Regression test: found live on a real document (appendix 537) - body
    text like "לתנאי הנספח 7 סייגי הפוליסה" ("per clause 7 of the appendix's
    terms") was matching "7" as if it were the appendix's own number, because
    "נספח" is a substring of "הנספח" ("the appendix") and the naive pattern
    had no left-side word boundary. "7" here is a clause number, not the
    appendix number (537, confirmed by the filename hint)."""
    file_path = tmp_path / "spvbabh5_1765276140_נספח_537_גילוי_נאות_מעודכן.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(
        file_path, "537 נספח מספר\n...\nלתנאי הנספח 7 סייגי הפוליסה וחריגיה מפורטים בסעיף"
    )

    assert fields["appendix_number"] == ["537"]


def test_multiple_appendix_numbers_on_one_page(tmp_path: Path) -> None:
    file_path = tmp_path / "3bdgqiqd_doc.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "נספחים 101, 102")

    assert fields["appendix_number"] == ["101", "102"]


def test_abbreviated_mispar_form_is_recognized(tmp_path: Path) -> None:
    """Life-domain documents phrase this as "נספח מס' <n>" (abbreviated
    "number"), not "נספח מספר <n>" (full word) - confirmed on a real life
    document. PyMuPDF tokenizes the apostrophe as its own word, so after RTL
    reconstruction the line reads "נספח מס ' 662"."""
    file_path = tmp_path / "d0od3kbm_doc.pdf"
    file_path.touch()
    extractor = HachsharaExtractor(HachsharaConfig())

    fields = extractor.extract_fields(file_path, "נספח מס ' 662")

    assert fields["appendix_number"] == ["662"]


def test_real_document_with_scrambled_rtl_header() -> None:
    """End-to-end regression test against a real downloaded Hachshara
    document (media/3bdgqiqd/..._נספח_531_גילוי_נאות_102023.pdf). This is
    the exact case that motivated reading_order.py: PyMuPDF's get_text()
    plain-text stream emits the page-1 header "מספר נספח 531" (visual
    order) as "531 נספח מספר" (stream order), so only the bbox-based
    reconstruction path finds it here - a synthetic fitz-built PDF would
    not reproduce this, since fitz's own text-insertion naturally writes in
    correct stream order.
    """
    file_path = _FIXTURES_DIR / "appendix_531_giluy_naot.pdf"
    assert file_path.is_file(), "real sample fixture is missing"

    extractor = HachsharaExtractor(HachsharaConfig())
    parser = HachsharaParser(HachsharaConfig())
    text = parser.extract_text(file_path)

    fields = extractor.extract_fields(file_path, text)

    assert fields["appendix_number"] == ["531"]


def test_real_life_document_with_abbreviated_mispar_form() -> None:
    """End-to-end regression test against a real downloaded life-domain
    document (media/d0od3kbm/..._נספח_662_...pdf) - the phrasing here is
    "נספח מס' 662" (abbreviated), a second real-world variant on top of the
    "נספח מספר 531" (full word) case covered above."""
    file_path = _FIXTURES_DIR / "appendix_662_life_giluy_naot.pdf"
    assert file_path.is_file(), "real sample fixture is missing"

    extractor = HachsharaExtractor(HachsharaConfig())
    parser = HachsharaParser(HachsharaConfig())
    text = parser.extract_text(file_path)

    fields = extractor.extract_fields(file_path, text)

    assert fields["appendix_number"] == ["662"]
