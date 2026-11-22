"""Endpoints behind the client review page (frontend/public/review.html).

A client reviewer marks each proposed cross-company match correct / partial /
wrong and may leave a note; every mark is saved here immediately so the team
sees it live on the same page. Access needs the shared REVIEW_ACCESS_CODE
(sent by the page as the X-Review-Code header, taken from the link's ?k=).
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from sqlalchemy import select

from core.config.settings import get_settings
from core.database.models import ReviewVerdict
from core.database.session import session_scope

router = APIRouter(prefix="/api/review")

_KEY_PATTERN = re.compile(r"^c-[A-Za-z0-9_-]{1,120}$")
_MAX_ENTRY_BYTES = 20_000


def require_review_code(x_review_code: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().review_access_code
    if not expected:
        raise HTTPException(status_code=503, detail="Review page is not enabled on this server")
    if not x_review_code or not secrets.compare_digest(x_review_code, expected):
        raise HTTPException(status_code=401, detail="Wrong or missing review code")


@router.get("/check", status_code=204, dependencies=[Depends(require_review_code)])
def check_code() -> Response:
    """Lets the page validate a code before showing the list."""
    return Response(status_code=204)


@router.get("/verdicts", dependencies=[Depends(require_review_code)])
def list_verdicts() -> dict[str, dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(select(ReviewVerdict)).all()
        return {row.key: row.data for row in rows}


@router.put("/verdicts/{key}", dependencies=[Depends(require_review_code)])
def save_verdict(key: str, data: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    if not _KEY_PATTERN.match(key):
        raise HTTPException(status_code=422, detail="Invalid key")
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > _MAX_ENTRY_BYTES:
        raise HTTPException(status_code=413, detail="Entry too large")
    with session_scope() as session:
        row = session.get(ReviewVerdict, key)
        if row is None:
            session.add(ReviewVerdict(key=key, data=data))
        else:
            row.data = data
    return data
