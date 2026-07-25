from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from core.exceptions import PdfProcessingError
from core.pdf_processing.document import PdfDocument


def _make_pdf(tmp_path: Path, *, pages_with_text: list[str]) -> Path:
    """Build a small synthetic PDF: one page per string (empty string = blank page)."""
    doc = fitz.open()
    for text in pages_with_text:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    path = tmp_path / "sample.pdf"
    doc.save(path)
    doc.close()
    return path


def test_page_count(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["one", "two"])
    with PdfDocument(path) as doc:
        assert doc.page_count == 2


def test_extract_text_with_text_layer(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["Appendix 101 hello world, this is a real page"])
    with PdfDocument(path) as doc:
        text = doc.extract_text(0)
        assert "Appendix 101" in text
        assert doc.has_text_layer(0)


def test_has_text_layer_false_for_garbled_text_with_no_real_letters(tmp_path: Path) -> None:
    """Regression test for a confirmed real-world failure mode: some older
    PDFs embed a font with a broken/missing ToUnicode mapping, so
    get_text() succeeds and returns plenty of characters (whitespace, stray
    punctuation) - long enough to pass a plain length check - but zero
    real letters. Only counting actual letters catches this and correctly
    falls back to OCR instead of feeding garbage downstream."""
    path = _make_pdf(tmp_path, pages_with_text=["!@#$%^&*()_+-=[]{}:;,.?~`|\\/<>1234567890"])
    with PdfDocument(path) as doc:
        text = doc.extract_text(0)
        assert len(text.strip()) >= 20  # long enough that the old check would pass
        assert not doc.has_text_layer(0)  # but the new one correctly rejects it


def test_extract_text_without_text_layer(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=[""])
    with PdfDocument(path) as doc:
        assert doc.extract_text(0).strip() == ""
        assert not doc.has_text_layer(0)


def test_extract_all_text_joins_pages(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["first page", "second page"])
    with PdfDocument(path) as doc:
        combined = doc.extract_all_text()
        assert "first page" in combined
        assert "second page" in combined


def test_render_page_to_image_shape(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["render me"])
    with PdfDocument(path) as doc:
        image = doc.render_page_to_image(0, dpi=100)
        assert image.ndim == 3
        assert image.shape[2] == 3
        assert image.dtype.name == "uint8"
        assert image.shape[0] > 0 and image.shape[1] > 0


def test_extract_words_returns_bboxes_for_inserted_text(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["hello world"])
    with PdfDocument(path) as doc:
        words = doc.extract_words(0)
        texts = [w[4] for w in words]
        assert texts == ["hello", "world"]
        for x0, y0, x1, y1, _text, *_rest in words:
            assert x1 > x0
            assert y1 > y0


def test_extract_words_empty_page(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=[""])
    with PdfDocument(path) as doc:
        assert doc.extract_words(0) == []


def test_page_size(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["sized page"])
    with PdfDocument(path) as doc:
        width, height = doc.page_size(0)
        # fitz.open().new_page() defaults to A4 (595 x 842 pt)
        assert width == pytest.approx(595, abs=1)
        assert height == pytest.approx(842, abs=1)


def test_page_index_out_of_range_raises(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["only page"])
    with PdfDocument(path) as doc:
        with pytest.raises(PdfProcessingError):
            doc.extract_text(5)


def test_open_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PdfProcessingError):
        PdfDocument(tmp_path / "does_not_exist.pdf")
