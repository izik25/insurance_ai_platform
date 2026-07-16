"""Phoenix OCR crop hints.

Not needed: Phoenix documents don't require OCR at all (see extractor.py).
"""

from __future__ import annotations

from core.plugins.base import BaseRules


class PhoenixRules(BaseRules):
    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        return []
