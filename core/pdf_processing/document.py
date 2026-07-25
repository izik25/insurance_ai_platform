"""PDF loading, embedded-text extraction, and page rendering (PyMuPDF)."""

from __future__ import annotations

import re
from pathlib import Path
from types import TracebackType

import fitz
import numpy as np

from core.exceptions import PdfProcessingError

_LETTER_RE = re.compile(r"[A-Za-zא-ת]")


class PdfDocument:
    """A PDF opened via PyMuPDF, with text extraction and page rendering.

    Use as a context manager to guarantee the underlying file handle closes:

        with PdfDocument(path) as doc:
            text = doc.extract_text(0)
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._doc = fitz.open(path)
        except Exception as exc:  # noqa: BLE001 - fitz raises assorted exception types
            raise PdfProcessingError(f"Failed to open PDF '{path}': {exc}") from exc

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def _check_page_index(self, page_index: int) -> None:
        if not 0 <= page_index < self.page_count:
            raise PdfProcessingError(
                f"Page {page_index} out of range for '{self.path}' ({self.page_count} pages)"
            )

    def extract_text(self, page_index: int) -> str:
        """Return the embedded text layer of one page, or '' if it has none."""
        self._check_page_index(page_index)
        return self._doc[page_index].get_text()

    def extract_all_text(self) -> str:
        return "\n".join(self.extract_text(i) for i in range(self.page_count))

    def extract_words(
        self, page_index: int
    ) -> list[tuple[float, float, float, float, str, int, int, int]]:
        """Return this page's words as (x0, y0, x1, y1, text, block_no, line_no, word_no).

        Coordinates are in PDF points from the page's top-left. Note that for
        some PDF generators, word order here (and in get_text()'s plain-text
        stream) does not match visual reading order for RTL text - see
        core/pdf_processing/reading_order.py.
        """
        self._check_page_index(page_index)
        return self._doc[page_index].get_text("words")

    def page_size(self, page_index: int) -> tuple[float, float]:
        """Return (width, height) of one page, in PDF points."""
        self._check_page_index(page_index)
        rect = self._doc[page_index].rect
        return (rect.width, rect.height)

    def has_text_layer(self, page_index: int, min_chars: int = 20) -> bool:
        """Heuristic: does this page carry a usable embedded text layer?

        Scanned documents produce empty or near-empty text even though
        get_text() succeeds, so a short length threshold distinguishes a
        real text layer from OCR-noise-free scans. Counting actual letters
        (not just any non-whitespace character) also catches a second,
        confirmed-live failure mode: some older PDFs embed a font with a
        broken/missing ToUnicode mapping, so get_text() succeeds and
        returns plenty of *characters* (whitespace, stray punctuation) but
        zero real letters - a plain `len(text.strip())` check would wrongly
        call that a usable text layer and never fall back to OCR, silently
        feeding garbage into every downstream consumer.
        """
        text = self.extract_text(page_index)
        return len(_LETTER_RE.findall(text)) >= min_chars

    def render_page_to_image(self, page_index: int, dpi: int = 200) -> np.ndarray:
        """Rasterize a page to an RGB uint8 array of shape (height, width, 3)."""
        self._check_page_index(page_index)
        pixmap = self._doc[page_index].get_pixmap(dpi=dpi)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        if pixmap.n == 4:
            return np.ascontiguousarray(image[:, :, :3])
        if pixmap.n == 1:
            return np.repeat(image, 3, axis=2)
        return image

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> PdfDocument:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
