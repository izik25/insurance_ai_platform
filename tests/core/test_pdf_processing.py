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
    path = _make_pdf(tmp_path, pages_with_text=["Appendix 101 hello world"])
    with PdfDocument(path) as doc:
        text = doc.extract_text(0)
        assert "Appendix 101" in text
        assert doc.has_text_layer(0)


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


def test_page_index_out_of_range_raises(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, pages_with_text=["only page"])
    with PdfDocument(path) as doc:
        with pytest.raises(PdfProcessingError):
            doc.extract_text(5)


def test_open_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PdfProcessingError):
        PdfDocument(tmp_path / "does_not_exist.pdf")
