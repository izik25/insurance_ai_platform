"""Contracts every insurance-company plugin (companies/<name>/) must implement.

A company module is responsible for four independent concerns, each with
its own abstract base here: downloading its own documents, parsing them
without OCR, extracting identity fields, and (optionally) providing
company-specific OCR crop rules. The `template_company` module (Stage 2)
is the reference implementation of this contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


class CompanyConfig(BaseModel):
    """Base configuration every company plugin extends with its own fields."""

    company_id: str
    display_name: str
    enabled: bool = True


class BaseDownloader(ABC):
    """Fetches source PDF documents for one insurance company."""

    def __init__(self, config: CompanyConfig) -> None:
        self.config = config

    @abstractmethod
    def download_all(self, destination_dir: Path, limit: int | None = None) -> list[Path]:
        """Download available documents, returning the local file paths.

        `limit`, if given, caps how many documents are downloaded — useful
        for smoke-testing a downloader against a live site before running
        it against the full archive.
        """


class BaseParser(ABC):
    """Reads a downloaded PDF and returns its embedded text, without OCR."""

    def __init__(self, config: CompanyConfig) -> None:
        self.config = config

    @abstractmethod
    def extract_text(self, file_path: Path) -> str:
        """Return the document's embedded text, or '' if it has none."""


class BaseExtractor(ABC):
    """Derives identity fields (policy/appendix numbers, names) from a document.

    Takes both the file path and its (possibly empty) parsed text: some
    companies encode hints in the file path itself (e.g. Migdal's filename
    doubles as its appendix number) that a real extractor cross-checks
    against the page content rather than trusting blindly. When `text` is
    empty (no embedded text layer — a scanned document), the extractor is
    responsible for falling back to OCR itself, using `BaseRules` for
    where to look.
    """

    def __init__(self, config: CompanyConfig) -> None:
        self.config = config

    @abstractmethod
    def extract_fields(self, file_path: Path, text: str) -> dict[str, list[str] | str | None]:
        """Return whatever identity fields can be found for this document."""


class BaseRules(ABC):
    """Company-specific hints consumed by the OCR stage (Stage 3)."""

    def __init__(self, config: CompanyConfig) -> None:
        self.config = config

    @abstractmethod
    def get_ocr_crop_regions(self, page_number: int) -> list[tuple[float, float, float, float]]:
        """Return normalized (x0, y0, x1, y1) crop boxes to OCR on a page."""


@dataclass(frozen=True)
class CompanyPlugin:
    """Everything the registry needs to operate on one insurance company."""

    config: CompanyConfig
    downloader: BaseDownloader
    parser: BaseParser
    extractor: BaseExtractor
    rules: BaseRules
