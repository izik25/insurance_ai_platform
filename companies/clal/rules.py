"""Clal OCR crop hints.

Not needed: Clal documents don't require OCR at all (see extractor.py) -
the appendix number already comes from the search API's metadata.
"""

from __future__ import annotations

from core.plugins.base import BaseRules


class ClalRules(BaseRules):
    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        return []
