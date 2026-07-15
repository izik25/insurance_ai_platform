from __future__ import annotations

from pathlib import Path

import fitz
import numpy as np

from companies.migdal.config import MigdalConfig
from companies.migdal.extractor import MigdalExtractor
from core.ocr.engine import OcrResult


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
    file_path = tmp_path / "7736_101.pdf"
    file_path.touch()
    extractor = MigdalExtractor(MigdalConfig())

    fields = extractor.extract_fields(file_path, "...\nנספח 101\n")

    assert fields["appendix_number"] == ["101"]


def test_page_text_trusted_over_mismatched_hint(tmp_path: Path) -> None:
    file_path = tmp_path / "7736_101.pdf"
    file_path.touch()
    extractor = MigdalExtractor(MigdalConfig())

    # filename says "101" but the page actually shows "102"
    fields = extractor.extract_fields(file_path, "...\nנספח 102\n")

    assert fields["appendix_number"] == ["102"]


def test_falls_back_to_filename_hint_when_no_text_found(tmp_path: Path) -> None:
    file_path = tmp_path / "7736_101.pdf"
    file_path.touch()
    extractor = MigdalExtractor(MigdalConfig())

    fields = extractor.extract_fields(file_path, "some unrelated text with no mention")

    assert fields["appendix_number"] == ["101"]


def test_non_numeric_filename_and_no_match_yields_empty(tmp_path: Path) -> None:
    file_path = tmp_path / "26724_ktav-sherut-bikur-rofe-hangasha.pdf"
    file_path.touch()
    extractor = MigdalExtractor(MigdalConfig())

    fields = extractor.extract_fields(file_path, "some unrelated text")

    assert fields["appendix_number"] == []


def test_ocr_fallback_used_when_no_embedded_text(tmp_path: Path) -> None:
    file_path = _make_blank_pdf(tmp_path, "7736_101.pdf")
    ocr_engine = _StubOcrEngine(text="שורת רעש\nנספח 101")
    extractor = MigdalExtractor(MigdalConfig(), ocr_engine=ocr_engine)

    fields = extractor.extract_fields(file_path, "")  # no embedded text -> triggers OCR

    assert fields["appendix_number"] == ["101"]


def test_no_ocr_engine_and_no_embedded_text_falls_back_to_hint(tmp_path: Path) -> None:
    file_path = _make_blank_pdf(tmp_path, "7736_101.pdf")
    extractor = MigdalExtractor(MigdalConfig(), ocr_engine=None)

    fields = extractor.extract_fields(file_path, "")

    assert fields["appendix_number"] == ["101"]


def test_multiple_appendix_numbers_on_one_page(tmp_path: Path) -> None:
    file_path = tmp_path / "7736_101.pdf"
    file_path.touch()
    extractor = MigdalExtractor(MigdalConfig())

    fields = extractor.extract_fields(file_path, "נספחים 101, 102")

    assert fields["appendix_number"] == ["101", "102"]
