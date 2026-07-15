from __future__ import annotations

import numpy as np

from core.ocr.preprocessing import denoise, deskew, sharpen, threshold, to_grayscale


def _text_like_image() -> np.ndarray:
    """A synthetic RGB image with a block of dark pixels on a light background."""
    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    image[80:120, 50:350] = 0
    return image


def test_to_grayscale_reduces_channels() -> None:
    gray = to_grayscale(_text_like_image())
    assert gray.ndim == 2


def test_to_grayscale_is_noop_on_already_gray() -> None:
    gray_in = np.zeros((10, 10), dtype=np.uint8)
    assert to_grayscale(gray_in) is gray_in


def test_deskew_preserves_shape() -> None:
    image = _text_like_image()
    result = deskew(image)
    assert result.shape == image.shape


def test_deskew_handles_blank_image() -> None:
    blank = np.full((100, 100, 3), 255, dtype=np.uint8)
    result = deskew(blank)  # must not raise despite no ink to estimate an angle
    assert result.shape == blank.shape


def test_denoise_preserves_shape() -> None:
    image = _text_like_image()
    assert denoise(image).shape == image.shape


def test_sharpen_preserves_shape() -> None:
    image = _text_like_image()
    assert sharpen(image).shape == image.shape


def test_threshold_produces_binary_image() -> None:
    image = _text_like_image()
    result = threshold(image)
    assert result.ndim == 2
    assert set(np.unique(result)).issubset({0, 255})
