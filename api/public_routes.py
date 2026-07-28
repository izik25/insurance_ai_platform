"""Public appendix/comparison API.

Unauthenticated, meant for external callers (unlike api/routes.py, the
internal dashboard API restricted to the dashboard's own CORS origins) - this
is the surface an outside system would actually integrate against to browse
a company's appendices, read what was extracted from one, see which
appendices at *other* companies it matches (the cross-company comparison),
and download the source file. Everything is keyed by company_id +
appendix_number, never by our internal document_id, since that's the
identity an external caller actually has.

Route registration order matters here: {appendix_number:path} is greedy (it
has to accept appendix numbers that themselves contain "/", e.g. "09/2023" -
see the regression test for it), so the routes with a literal suffix
(/matches, /file) must be declared before the bare detail route, or the
detail route's pattern would swallow the suffix as part of the appendix
number and shadow the more specific routes entirely.
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.file_serving import build_file_response, resolve_document_file_path
from api.public_schemas import (
    PublicAppendixDetail,
    PublicAppendixMatch,
    PublicAppendixSummary,
    PublicCompanyOut,
    PublicPolicyTableOut,
)
from core.database.models import Company, Document, DocumentExtraction, DocumentMatch
from core.database.session import session_scope
from core.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/public/v1")


def _find_document(session: Session, company_id: str, appendix_number: str) -> Document:
    candidates = session.scalars(
        select(Document)
        .where(Document.company_id == company_id)
        # postgresql.ARRAY's Comparator.any() (value = ANY(array)) is correct
        # at runtime; mypy resolves it against the relationship Comparator's
        # any() signature instead, hence the ignore.
        .where(Document.appendix_number.any(appendix_number))  # type: ignore[arg-type]
        .order_by(Document.created_date.desc())
    ).all()

    if not candidates:
        raise HTTPException(status_code=404, detail="No appendix found for this company/number")

    if len(candidates) > 1:
        # A company can occasionally have more than one document tagged with
        # the same appendix number (re-uploads, revisions, etc.) - resolve to
        # the most recently ingested one rather than failing, but log it so
        # it's visible when auditing data quality.
        logger.warning(
            "Multiple documents match company_id=%s appendix_number=%s (%d matches) - "
            "resolving to the most recent",
            company_id,
            appendix_number,
            len(candidates),
        )

    return candidates[0]


@router.get("/companies", response_model=list[PublicCompanyOut])
def list_companies() -> list[PublicCompanyOut]:
    """All insurers with at least one processed appendix, and how many they have."""
    with session_scope() as session:
        companies = session.scalars(select(Company)).all()
        counts = Counter(session.scalars(select(Document.company_id)))
        return [
            PublicCompanyOut(
                id=c.id, display_name=c.display_name, appendix_count=counts.get(c.id, 0)
            )
            for c in companies
        ]


@router.get("/companies/{company_id}/appendices", response_model=list[PublicAppendixSummary])
def list_appendices(company_id: str, domain: str | None = None) -> list[PublicAppendixSummary]:
    """Browse a company's appendices, optionally filtered by domain (health/life/mixed)."""
    with session_scope() as session:
        query = select(Document).where(Document.company_id == company_id)
        if domain:
            query = query.where(Document.domain == domain)
        documents = session.scalars(query.order_by(Document.created_date.desc())).all()
        return [
            PublicAppendixSummary(
                company_id=d.company_id,
                appendix_number=d.appendix_number,
                appendix_name=d.appendix_name,
                domain=d.domain,
                pages_count=d.pages_count,
                created_date=d.created_date,
            )
            for d in documents
        ]


@router.get("/companies/{company_id}/appendices/{appendix_number:path}/file")
def get_appendix_file(
    company_id: str, appendix_number: str, download: bool = False
) -> FileResponse:
    """Return the source file for appendix `appendix_number` at `company_id`."""
    with session_scope() as session:
        document = _find_document(session, company_id, appendix_number)
        file_path = document.file_path
        original_file_name = document.original_file_name

    resolved_path = resolve_document_file_path(file_path)
    return build_file_response(resolved_path, original_file_name, download=download)


@router.get(
    "/companies/{company_id}/appendices/{appendix_number:path}/matches",
    response_model=list[PublicAppendixMatch],
)
def get_appendix_matches(
    company_id: str, appendix_number: str, status: str | None = None
) -> list[PublicAppendixMatch]:
    """The cross-company comparison: which appendices at *other* companies were
    matched to this one, with a similarity score and review status
    (auto_confirmed/pending_review/confirmed/rejected)."""
    with session_scope() as session:
        document = _find_document(session, company_id, appendix_number)

        query = select(DocumentMatch).where(
            (DocumentMatch.document_id == document.id)
            | (DocumentMatch.matched_document_id == document.id)
        )
        if status:
            query = query.where(DocumentMatch.status == status)
        matches = session.scalars(query).all()

        other_ids = {
            m.matched_document_id if m.document_id == document.id else m.document_id
            for m in matches
        }
        others_by_id = {
            d.id: d for d in session.scalars(select(Document).where(Document.id.in_(other_ids)))
        }

        results: list[PublicAppendixMatch] = []
        for m in matches:
            other_id = m.matched_document_id if m.document_id == document.id else m.document_id
            other = others_by_id.get(other_id)
            if other is None:
                continue
            results.append(
                PublicAppendixMatch(
                    company_id=other.company_id,
                    appendix_number=other.appendix_number,
                    appendix_name=other.appendix_name,
                    domain=other.domain,
                    similarity_score=m.similarity_score,
                    status=m.status,
                )
            )
        return results


@router.get(
    "/companies/{company_id}/appendices/{appendix_number:path}", response_model=PublicAppendixDetail
)
def get_appendix_detail(company_id: str, appendix_number: str) -> PublicAppendixDetail:
    """Everything extracted from appendix `appendix_number` at `company_id`."""
    with session_scope() as session:
        document = _find_document(session, company_id, appendix_number)
        extraction = (
            session.query(DocumentExtraction).filter_by(document_id=document.id).one_or_none()
        )
        return PublicAppendixDetail(
            company_id=document.company_id,
            appendix_number=document.appendix_number,
            appendix_name=document.appendix_name,
            domain=document.domain,
            pages_count=document.pages_count,
            has_extraction=extraction is not None,
            coverage_type=extraction.coverage_type if extraction else None,
            coverage_name=extraction.coverage_name if extraction else None,
            eligibility_conditions=extraction.eligibility_conditions if extraction else None,
            insurance_amounts=extraction.insurance_amounts if extraction else [],
            qualifying_period=extraction.qualifying_period if extraction else None,
            waiting_period=extraction.waiting_period if extraction else None,
            exclusions=extraction.exclusions if extraction else [],
            age_range=extraction.age_range if extraction else None,
            restrictions=extraction.restrictions if extraction else [],
            tables=[PublicPolicyTableOut(**t) for t in extraction.tables.get("tables", [])]
            if extraction
            else [],
            disease_count=extraction.disease_count if extraction else None,
            disease_list=extraction.disease_list if extraction else [],
            survival_period=extraction.survival_period if extraction else None,
            created_date=document.created_date,
        )
