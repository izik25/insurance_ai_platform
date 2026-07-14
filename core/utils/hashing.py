"""File hashing helpers, used to detect duplicate documents on ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_of_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of an in-memory buffer."""
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file, streamed to bound memory use."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
