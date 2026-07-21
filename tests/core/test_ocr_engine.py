"""Engine tests run against the real Tesseract binary (installed locally),
using English text — PIL's default font cannot shape Hebrew glyphs, so
Hebrew accuracy is validated separately against real scanned documents,
not via synthetically rendered images here."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.exceptions import OcrError
from core.ocr.engine import OcrResult, TesseractEngine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESSDATA_DIR = PROJECT_ROOT / "tessdata"

pytestmark = pytest.mark.skipif(
    not (TESSDATA_DIR / "eng.traineddata").exists(),
    reason="Tesseract eng tessdata not present — run scripts/setup_tessdata.py",
)


def _render_text_image(text: str) -> np.ndarray:
    image = Image.new("RGB", (400, 100), color="white")
    ImageDraw.Draw(image).text((10, 30), text, fill="black")
    return np.array(image)


def test_run_recognizes_english_text() -> None:
    engine = TesseractEngine(TESSDATA_DIR, lang="eng")
    result = engine.run(_render_text_image("Appendix 101"), preprocess=False)

    assert isinstance(result, OcrResult)
    assert "101" in result.text
    assert 0.0 <= result.confidence <= 1.0


def test_run_with_preprocessing_returns_a_result() -> None:
    # The preprocessing pipeline is tuned for noisy/skewed scans, not
    # small, already-clean synthetic renders — it may mangle this image,
    # so we only assert it runs cleanly and returns a well-formed result.
    engine = TesseractEngine(TESSDATA_DIR, lang="eng")
    result = engine.run(_render_text_image("Appendix 101"), preprocess=True)
    assert isinstance(result, OcrResult)
    assert 0.0 <= result.confidence <= 1.0


def test_run_blank_image_yields_empty_result() -> None:
    engine = TesseractEngine(TESSDATA_DIR, lang="eng")
    blank = np.full((100, 400, 3), 255, dtype=np.uint8)
    result = engine.run(blank, preprocess=False)
    assert result.text == ""
    assert result.confidence == 0.0


def test_run_converts_tesseract_timeout_to_ocr_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung/slow OCR subprocess must surface as a catchable error instead
    of blocking forever - a real extraction run once sat blocked for ~12
    hours on one page because no timeout was set and nothing was ever
    raised. pytesseract signals a timeout with a plain RuntimeError."""

    def _raise_timeout(*args: object, **kwargs: object) -> str:
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr("core.ocr.engine.pytesseract.image_to_string", _raise_timeout)

    engine = TesseractEngine(TESSDATA_DIR, lang="eng", timeout_seconds=0.01)
    with pytest.raises(OcrError):
        engine.run(_render_text_image("Appendix 101"), preprocess=False)


def test_save_result_writes_json(tmp_path: Path) -> None:
    result = OcrResult(text="נספח 101", confidence=0.93)
    destination = tmp_path / "nested" / "result.json"

    TesseractEngine.save_result(result, destination)

    content = destination.read_text(encoding="utf-8")
    assert "נספח 101" in content
    assert "0.93" in content
