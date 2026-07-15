from __future__ import annotations

import numpy as np
import pytest

from core.ocr.regions import crop_region


def test_crop_region_extracts_expected_pixels() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[80:, :] = 255  # bottom 20% is white

    cropped = crop_region(image, (0.0, 0.8, 1.0, 1.0))

    assert cropped.shape == (20, 200, 3)
    assert (cropped == 255).all()


@pytest.mark.parametrize(
    "region",
    [
        (0.5, 0.0, 0.2, 1.0),  # x0 > x1
        (0.0, 0.5, 1.0, 0.2),  # y0 > y1
        (-0.1, 0.0, 1.0, 1.0),  # out of range
        (0.0, 0.0, 1.1, 1.0),  # out of range
    ],
)
def test_crop_region_rejects_invalid_bounds(
    region: tuple[float, float, float, float],
) -> None:
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="Invalid normalized crop region"):
        crop_region(image, region)
