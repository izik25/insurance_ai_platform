"""Cropping helpers for targeted (region-of-interest) OCR.

Crop regions are normalized (x0, y0, x1, y1) tuples in the 0.0-1.0 range —
independent of DPI/resolution, matching `BaseRules.get_ocr_crop_regions`.
"""

from __future__ import annotations

import numpy as np


def crop_region(image: np.ndarray, region: tuple[float, float, float, float]) -> np.ndarray:
    """Crop `image` to a normalized (x0, y0, x1, y1) region."""
    x0, y0, x1, y1 = region
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise ValueError(f"Invalid normalized crop region: {region}")

    height, width = image.shape[:2]
    px0, px1 = int(x0 * width), int(x1 * width)
    py0, py1 = int(y0 * height), int(y1 * height)
    return image[py0:py1, px0:px1]
