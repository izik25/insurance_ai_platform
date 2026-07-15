from __future__ import annotations

from pathlib import Path

import fitz

from companies.migdal.config import MigdalConfig
from companies.migdal.parser import MigdalParser


def _make_pdf(tmp_path: Path, text: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    path = tmp_path / "sample.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extract_text_returns_embedded_text(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, "appendix 101")
    parser = MigdalParser(MigdalConfig())
    assert "appendix 101" in parser.extract_text(path)


def test_extract_text_empty_for_blank_page(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path, "")
    parser = MigdalParser(MigdalConfig())
    assert parser.extract_text(path).strip() == ""


def test_extract_text_missing_file_returns_empty_string(tmp_path: Path) -> None:
    parser = MigdalParser(MigdalConfig())
    assert parser.extract_text(tmp_path / "does_not_exist.pdf") == ""
