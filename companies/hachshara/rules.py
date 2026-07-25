"""Hachshara OCR crop hints.

The one confirmed sample document has a real embedded text layer, but not
every one of the ~155 health documents is guaranteed to (older
disclosures/archive items could be scans) - defensive fallback region for
extractor.py, same principle as Migdal's footer crop rule. The appendix
number/document code sits near the top of page 1 (confirmed live: y=90-127
of an 842pt-tall page, right under the document title), so the crop targets
the top ~20% of the page rather than Migdal's bottom-15% footer.
"""

from __future__ import annotations

from core.plugins.base import BaseRules

_FIRST_PAGE_HEADER = (0.0, 0.0, 1.0, 0.20)


class HachsharaRules(BaseRules):
    """Crop regions for Hachshara's policy/appendix documents."""

    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        if page_number == 0:
            return [_FIRST_PAGE_HEADER]
        return []
