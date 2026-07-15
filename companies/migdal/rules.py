"""Migdal-specific OCR crop hints.

Minimal placeholder satisfying the BaseRules contract. Real crop regions
(where on the page the appendix number typically sits) are defined once
Stage 3 builds the OCR pipeline and we can inspect real scanned samples.
"""

from __future__ import annotations

from core.plugins.base import BaseRules


class MigdalRules(BaseRules):
    """Stage 3 will replace this with real OCR crop regions."""

    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        return []
