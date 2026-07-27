"""AIG OCR crop hints.

Not needed: documents don't require OCR at all (see extractor.py) - the
title, when a real one exists, already comes from the product page's link
text.
"""

from __future__ import annotations

from core.plugins.base import BaseRules


class AigRules(BaseRules):
    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        return []
