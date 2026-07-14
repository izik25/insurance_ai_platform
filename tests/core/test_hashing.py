from __future__ import annotations

import hashlib
from pathlib import Path

from core.utils.hashing import sha256_of_bytes, sha256_of_file


def test_sha256_of_bytes_matches_hashlib() -> None:
    data = b"insurance document bytes"
    assert sha256_of_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_of_file_matches_bytes(tmp_path: Path) -> None:
    data = b"x" * (2 * 1024 * 1024 + 17)  # spans multiple read chunks
    file_path = tmp_path / "sample.bin"
    file_path.write_bytes(data)
    assert sha256_of_file(file_path) == sha256_of_bytes(data)


def test_different_content_yields_different_hash() -> None:
    assert sha256_of_bytes(b"a") != sha256_of_bytes(b"b")
