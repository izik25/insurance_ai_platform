"""Shared helpers for serving a document's original source file.

Used by both the internal dashboard API (api/routes.py) and the public
appendix-lookup API (api/public_routes.py), so the path-traversal guard and
FileResponse construction only live in one place.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from core.config.settings import get_settings


def resolve_document_file_path(file_path: str) -> Path:
    """Resolve a document's stored relative file_path under raw_documents_dir.

    file_path is server-side data, not attacker-controlled, but this guards
    against a corrupted/misconfigured row pointing outside the documents
    directory.
    """
    settings = get_settings()
    raw_documents_dir = settings.raw_documents_dir.resolve()
    resolved_path = (raw_documents_dir / file_path).resolve()
    if resolved_path != raw_documents_dir and raw_documents_dir not in resolved_path.parents:
        raise HTTPException(status_code=404, detail="Document file not found")
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found on disk")
    return resolved_path


def build_file_response(
    resolved_path: Path, original_file_name: str, *, download: bool
) -> FileResponse:
    media_type, _ = mimetypes.guess_type(original_file_name)
    return FileResponse(
        resolved_path,
        media_type=media_type or "application/octet-stream",
        filename=original_file_name,
        content_disposition_type="attachment" if download else "inline",
    )
