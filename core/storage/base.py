"""Storage backend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """Persists and retrieves file content addressed by a relative path."""

    @abstractmethod
    def save(self, relative_path: str | Path, data: bytes) -> Path:
        """Persist `data` at `relative_path` and return the resolved path."""

    @abstractmethod
    def read(self, relative_path: str | Path) -> bytes:
        """Return the raw bytes stored at `relative_path`."""

    @abstractmethod
    def exists(self, relative_path: str | Path) -> bool:
        """Return whether something is stored at `relative_path`."""

    @abstractmethod
    def delete(self, relative_path: str | Path) -> None:
        """Remove whatever is stored at `relative_path`, if present."""
