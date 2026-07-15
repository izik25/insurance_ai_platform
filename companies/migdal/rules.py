"""Migdal-specific OCR crop hints.

Validated against a real scanned document (7736_101.pdf): the appendix
number footer sits in roughly the bottom 15% of page 1. Later pages can
repeat it too, but page 1 alone was sufficient in testing, matching the
platform's "don't OCR more than necessary" principle.
"""

from __future__ import annotations

from core.plugins.base import BaseRules

_FIRST_PAGE_FOOTER = (0.0, 0.85, 1.0, 1.0)


class MigdalRules(BaseRules):
    """Crop regions for Migdal's scanned policy-terms documents."""

    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        if page_number == 0:
            return [_FIRST_PAGE_FOOTER]
        return []
