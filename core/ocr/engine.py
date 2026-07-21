"""Tesseract-based OCR engine.

PaddleOCR (the originally planned engine) ships no Hebrew recognition
model — confirmed by inspecting its installed language configs, which
cover ~20 scripts but not Hebrew. Tesseract, with the 'heb' tessdata file,
does, and was validated against real scanned Migdal documents.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pytesseract

from core.exceptions import OcrError
from core.ocr.preprocessing import preprocess_for_ocr
from core.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OcrResult:
    """Full OCR output for one image, per the platform's OCR spec."""

    text: str
    confidence: float  # 0.0-1.0, mean word-level confidence


class TesseractEngine:
    """Runs Tesseract OCR against a project-local tessdata directory.

    Tesseract's bundled tessdata (under its install dir, often
    admin-write-protected) usually only ships 'eng'. Rather than fight
    quoting issues passing `--tessdata-dir` as a config string (it breaks
    on paths containing spaces), this sets the TESSDATA_PREFIX environment
    variable once, process-wide — the standard, quoting-safe way to point
    Tesseract at a custom tessdata directory.
    """

    def __init__(self, tessdata_dir: Path, lang: str = "heb", timeout_seconds: float = 120.0) -> None:
        self.lang = lang
        self.timeout_seconds = timeout_seconds
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir.resolve())

        # Resolve the tesseract binary to an absolute path rather than
        # relying on PATH lookup at call time: subprocess creation for
        # unlisted executables loses PATH in some sandboxed environments,
        # even though it's set correctly in this process's own os.environ.
        resolved = shutil.which("tesseract")
        if resolved is None:
            windows_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
            resolved = str(windows_default) if windows_default.is_file() else None
        if resolved is not None:
            pytesseract.pytesseract.tesseract_cmd = resolved

    def run(self, image: np.ndarray, *, preprocess: bool = False) -> OcrResult:
        """Run OCR on an image (typically a cropped region of a page).

        `preprocess=False` is the default: measured against real scanned
        Migdal documents, the deskew/denoise/sharpen/threshold pipeline
        reduced recognition quality more often than it helped (already
        high-DPI, low-skew scans don't need it, and adaptive thresholding
        clipped thin Hebrew glyph strokes). Enable it for genuinely noisy
        or skewed sources where it may help.
        """
        ocr_input = preprocess_for_ocr(image) if preprocess else image
        try:
            text = pytesseract.image_to_string(
                ocr_input, lang=self.lang, timeout=self.timeout_seconds
            ).strip()
            data = pytesseract.image_to_data(
                ocr_input,
                lang=self.lang,
                output_type=pytesseract.Output.DICT,
                timeout=self.timeout_seconds,
            )
        except RuntimeError as exc:
            # pytesseract raises plain RuntimeError (not TesseractError) when
            # the subprocess is killed for exceeding `timeout`.
            raise OcrError(f"Tesseract timed out after {self.timeout_seconds}s: {exc}") from exc
        except pytesseract.TesseractError as exc:
            raise OcrError(f"Tesseract failed: {exc}") from exc

        confidences = [c for c in (float(c) for c in data["conf"]) if c >= 0]
        confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0

        logger.debug("OCR produced %d chars, confidence=%.2f", len(text), confidence)
        return OcrResult(text=text, confidence=confidence)

    @staticmethod
    def save_result(result: OcrResult, destination: Path) -> None:
        """Persist the full OCR text + confidence score as JSON."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
