"""Read-only dashboard endpoints: documents, extractions, and matches.

No LLM calls here - everything is a DB lookup against data already
produced by scripts/{extract,embed,match}_documents.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from api.schemas import (
    DocumentOut,
    ExtractionOut,
    MatchDocumentSummary,
    MatchOut,
    MatchStatusUpdate,
    PolicyTableOut,
)
from core.database.models import Document, DocumentEmbedding, DocumentExtraction, DocumentMatch
from core.database.session import session_scope

router = APIRouter(prefix="/api")


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(company_id: str | None = None, domain: str | None = None) -> list[DocumentOut]:
    with session_scope() as session:
        query = select(Document)
        if company_id:
            query = query.where(Document.company_id == company_id)
        if domain:
            query = query.where(Document.domain == domain)
        documents = session.scalars(query).all()

        extracted_ids = set(session.scalars(select(DocumentExtraction.document_id)))
        embedded_ids = set(session.scalars(select(DocumentEmbedding.document_id)))

        return [
            DocumentOut(
                id=d.id,
                company_id=d.company_id,
                domain=d.domain,
                original_file_name=d.original_file_name,
                appendix_number=d.appendix_number,
                appendix_name=d.appendix_name,
                pages_count=d.pages_count,
                extraction_method=d.extraction_method,
                has_extraction=d.id in extracted_ids,
                has_embedding=d.id in embedded_ids,
                created_date=d.created_date,
            )
            for d in documents
        ]


@router.get("/extractions/{document_id:path}", response_model=ExtractionOut)
def get_extraction(document_id: str) -> ExtractionOut:
    with session_scope() as session:
        row = session.query(DocumentExtraction).filter_by(document_id=document_id).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No extraction for this document")
        document = session.get(Document, document_id)
        return ExtractionOut(
            document_id=row.document_id,
            appendix_number=document.appendix_number if document else [],
            appendix_name=document.appendix_name if document else None,
            coverage_type=row.coverage_type,
            coverage_name=row.coverage_name,
            eligibility_conditions=row.eligibility_conditions,
            insurance_amounts=row.insurance_amounts,
            qualifying_period=row.qualifying_period,
            waiting_period=row.waiting_period,
            exclusions=row.exclusions,
            age_range=row.age_range,
            restrictions=row.restrictions,
            tables=[PolicyTableOut(**t) for t in row.tables.get("tables", [])],
            disease_count=row.disease_count,
            disease_list=row.disease_list,
            survival_period=row.survival_period,
            created_date=row.created_date,
        )


def _document_summary(document: Document) -> MatchDocumentSummary:
    return MatchDocumentSummary(
        id=document.id,
        company_id=document.company_id,
        domain=document.domain,
        original_file_name=document.original_file_name,
        appendix_number=document.appendix_number,
        appendix_name=document.appendix_name,
    )


@router.get("/matches", response_model=list[MatchOut])
def list_matches(
    status: str | None = Query(default=None), document_id: str | None = Query(default=None)
) -> list[MatchOut]:
    with session_scope() as session:
        query = select(DocumentMatch)
        if status:
            query = query.where(DocumentMatch.status == status)
        if document_id:
            query = query.where(
                (DocumentMatch.document_id == document_id)
                | (DocumentMatch.matched_document_id == document_id)
            )
        matches = session.scalars(query).all()

        document_ids = {m.document_id for m in matches} | {m.matched_document_id for m in matches}
        documents_by_id = {
            d.id: d for d in session.scalars(select(Document).where(Document.id.in_(document_ids)))
        }

        results: list[MatchOut] = []
        for match in matches:
            document = documents_by_id.get(match.document_id)
            matched_document = documents_by_id.get(match.matched_document_id)
            if document is None or matched_document is None:
                continue
            results.append(
                MatchOut(
                    id=match.id,
                    document=_document_summary(document),
                    matched_document=_document_summary(matched_document),
                    similarity_score=match.similarity_score,
                    status=match.status,
                    created_date=match.created_date,
                )
            )
        return results


@router.patch("/matches/{match_id:path}", response_model=MatchOut)
def update_match_status(match_id: str, body: MatchStatusUpdate) -> MatchOut:
    """Manual review decision from the dashboard: confirm or reject a
    proposed cross-company match after a human compares both documents."""
    with session_scope() as session:
        match = session.get(DocumentMatch, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        match.status = body.status

        document = session.get(Document, match.document_id)
        matched_document = session.get(Document, match.matched_document_id)
        if document is None or matched_document is None:
            raise HTTPException(status_code=404, detail="Document for this match not found")

        return MatchOut(
            id=match.id,
            document=_document_summary(document),
            matched_document=_document_summary(matched_document),
            similarity_score=match.similarity_score,
            status=match.status,
            created_date=match.created_date,
        )
