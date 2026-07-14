"""Local filesystem implementation of StorageBackend."""

from __future__ import annotations

from pathlib import Path

from core.exceptions import StorageError
from core.storage.base import StorageBackend
from core.utils.logging import get_logger

logger = get_logger(__name__)


class LocalFileStorage(StorageBackend):
    """Stores files under a fixed root directory on the local filesystem."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir.resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str | Path) -> Path:
        resolved = (self._base_dir / relative_path).resolve()
        if self._base_dir not in resolved.parents and resolved != self._base_dir:
            raise StorageError(f"Path '{relative_path}' escapes storage root")
        return resolved

    def save(self, relative_path: str | Path, data: bytes) -> Path:
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        logger.debug("Saved %d bytes to %s", len(data), target)
        return target

    def read(self, relative_path: str | Path) -> bytes:
        target = self._resolve(relative_path)
        if not target.is_file():
            raise StorageError(f"No file at '{relative_path}'")
        return target.read_bytes()

    def exists(self, relative_path: str | Path) -> bool:
        return self._resolve(relative_path).is_file()

    def delete(self, relative_path: str | Path) -> None:
        target = self._resolve(relative_path)
        if target.is_file():
            target.unlink()
            logger.debug("Deleted %s", target)
