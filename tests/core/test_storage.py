from __future__ import annotations

from pathlib import Path

import pytest

from core.exceptions import StorageError
from core.storage.local import LocalFileStorage


def test_save_read_exists_delete_roundtrip(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    data = b"hello world"

    saved_path = storage.save("sub/dir/file.txt", data)

    assert saved_path.is_file()
    assert storage.exists("sub/dir/file.txt")
    assert storage.read("sub/dir/file.txt") == data

    storage.delete("sub/dir/file.txt")
    assert not storage.exists("sub/dir/file.txt")


def test_read_missing_raises(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.read("missing.txt")


def test_path_traversal_rejected(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.save("../escape.txt", b"data")


def test_delete_missing_is_noop(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    storage.delete("never_existed.txt")  # must not raise
