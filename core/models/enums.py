"""Enumerations used by domain models."""

from __future__ import annotations

from enum import StrEnum


class DocumentType(StrEnum):
    """What kind of document this is, within a policy."""

    POLICY = "policy"
    APPENDIX = "appendix"
    OTHER = "other"


class ExtractionMethod(StrEnum):
    """How a document's identity fields were obtained."""

    TEXT = "text"
    OCR = "ocr"
    MANUAL = "manual"
