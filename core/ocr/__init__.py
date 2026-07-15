"""Targeted OCR: OpenCV preprocessing + Tesseract (Hebrew) recognition."""

from core.ocr.engine import OcrResult, TesseractEngine
from core.ocr.regions import crop_region

__all__ = ["OcrResult", "TesseractEngine", "crop_region"]
