"""Harel OCR crop hints.

Not needed: Harel documents don't require OCR for identity fields at all
(see extractor.py) - the appendix number already comes from the archive
table's own metadata.
"""

from __future__ import annotations

from core.plugins.base import BaseRules


class HarelRules(BaseRules):
    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        return []
