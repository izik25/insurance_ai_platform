"""OpenCV image preprocessing pipeline for OCR: deskew, denoise, sharpen, threshold."""

from __future__ import annotations

import cv2
import numpy as np

_SHARPEN_KERNEL = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])


def to_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def deskew(image: np.ndarray) -> np.ndarray:
    """Rotate the image to correct small skew, estimated from dark-pixel spread."""
    gray = to_grayscale(image)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(binary > 0))
    if coords.shape[0] < 10:
        return image  # not enough ink to estimate an angle

    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.1:
        return image

    height, width = image.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def denoise(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.fastNlMeansDenoising(image, h=10)
    return cv2.fastNlMeansDenoisingColored(image, h=10, hColor=10)


def sharpen(image: np.ndarray) -> np.ndarray:
    return cv2.filter2D(image, -1, _SHARPEN_KERNEL)


def threshold(image: np.ndarray) -> np.ndarray:
    gray = to_grayscale(image)
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )


def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    """Full pipeline: deskew -> denoise -> sharpen -> threshold."""
    image = deskew(image)
    image = denoise(image)
    image = sharpen(image)
    return threshold(image)
