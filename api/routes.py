"""Read-only dashboard endpoints: documents, extractions, and matches.

No LLM calls here - everything is a DB lookup against data already
produced by scripts/{extract,embed,match}_documents.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from api.file_serving import build_file_response, resolve_document_file_path
from api.schemas import (
    CanonicalProfileOut,
    ClassificationOut,
    DocumentAnalysisOut,
    DocumentOut,
    ExtractionOut,
    FingerprintOut,
    MatchDocumentSummary,
    MatchOut,
    MatchStatusUpdate,
    PolicyTableOut,
)
from core.database.models import (
    Document,
    DocumentCanonicalProfile,
    DocumentClassification,
    DocumentEmbedding,
    DocumentExtraction,
    DocumentFingerprint,
    DocumentMatch,
)
from core.database.session import session_scope

router = APIRouter(prefix="/api")


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    company_id: str | None = None, domain: str | None = None, main_category: str | None = None
) -> list[DocumentOut]:
    with session_scope() as session:
        query = select(Document)
        if company_id:
            query = query.where(Document.company_id == company_id)
        if domain:
            query = query.where(Document.domain == domain)
        documents = session.scalars(query).all()

        extracted_ids = set(session.scalars(select(DocumentExtraction.document_id)))
        embedded_ids = set(session.scalars(select(DocumentEmbedding.document_id)))
        classifications_by_doc = {
            row.document_id: row for row in session.scalars(select(DocumentClassification))
        }

        results = []
        for d in documents:
            classification = classifications_by_doc.get(d.id)
            if main_category and (classification is None or classification.main_category != main_category):
                continue
            results.append(
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
                    marketing_start_date=d.marketing_start_date,
                    marketing_end_date=d.marketing_end_date,
                    is_active=d.is_active,
                    category_id=classification.category_id if classification else None,
                    main_category=classification.main_category if classification else None,
                    coverage_family=classification.coverage_family if classification else None,
                    coverage_subtype=classification.coverage_subtype if classification else None,
                    created_date=d.created_date,
                )
            )
        return results


@router.get("/documents/{document_id:path}/analysis", response_model=DocumentAnalysisOut)
def get_document_analysis(document_id: str) -> DocumentAnalysisOut:
    """Everything the taxonomy/matching upgrade pipeline has computed for
    this document so far (classification / canonical profile / fingerprint)
    - each section is null until that stage has actually run for this
    document; never computed on request (no LLM call happens here)."""
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")

        classification_row = session.scalar(
            select(DocumentClassification).where(DocumentClassification.document_id == document_id)
        )
        profile_row = session.scalar(
            select(DocumentCanonicalProfile).where(DocumentCanonicalProfile.document_id == document_id)
        )
        fingerprint_row = session.scalar(
            select(DocumentFingerprint).where(DocumentFingerprint.document_id == document_id)
        )

        classification = (
            ClassificationOut(
                category_id=classification_row.category_id,
                main_category=classification_row.main_category,
                coverage_family=classification_row.coverage_family,
                coverage_subtype=classification_row.coverage_subtype,
                coverage_variant=classification_row.coverage_variant,
                benefit_model=classification_row.benefit_model,
                target_population=classification_row.target_population,
                confidence=classification_row.confidence,
                evidence=classification_row.evidence,
            )
            if classification_row
            else None
        )

        canonical_profile = (
            CanonicalProfileOut(
                insured_event=profile_row.insured_event,
                covered_events=profile_row.covered_events,
                covered_conditions=profile_row.covered_conditions,
                exclusions_normalized=profile_row.exclusions_normalized,
                limitations=profile_row.limitations,
                eligibility_normalized=profile_row.eligibility_normalized,
                waiting_period_text=profile_row.raw_profile.get("waiting_period_text"),
                qualifying_period_text=profile_row.raw_profile.get("qualifying_period_text"),
                survival_period_text=profile_row.raw_profile.get("survival_period_text"),
                benefit_type=profile_row.benefit_type,
                benefit_calculation=profile_row.benefit_calculation,
                amounts=profile_row.amounts,
                caps=profile_row.caps,
                additional_findings_summary=profile_row.additional_findings_summary,
            )
            if profile_row
            else None
        )

        fingerprint = (
            FingerprintOut(
                waiting_period_days=fingerprint_row.waiting_period_days,
                qualifying_period_days=fingerprint_row.qualifying_period_days,
                survival_period_days=fingerprint_row.survival_period_days,
                min_entry_age=fingerprint_row.min_entry_age,
                max_entry_age=fingerprint_row.max_entry_age,
                termination_age=fingerprint_row.termination_age,
                benefit_type=fingerprint_row.benefit_type,
                benefit_amount_min=fingerprint_row.benefit_amount_min,
                benefit_amount_max=fingerprint_row.benefit_amount_max,
                benefit_amount_currency=fingerprint_row.benefit_amount_currency,
                benefit_percentage=fingerprint_row.benefit_percentage,
                maximum_benefit=fingerprint_row.maximum_benefit,
                deductible_amount=fingerprint_row.deductible_amount,
                covered_event_count=fingerprint_row.covered_event_count,
                major_exclusion_count=fingerprint_row.major_exclusion_count,
                special_condition_count=fingerprint_row.special_condition_count,
            )
            if fingerprint_row
            else None
        )

        return DocumentAnalysisOut(
            document_id=document_id,
            classification=classification,
            canonical_profile=canonical_profile,
            fingerprint=fingerprint,
        )


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


@router.get("/documents/{document_id:path}/file")
def get_document_file(document_id: str, download: bool = False) -> FileResponse:
    """Serve the original source file for a document, so a reviewer can
    open (or download) it to verify extracted/matched data against it."""
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        file_path = document.file_path
        original_file_name = document.original_file_name

    resolved_path = resolve_document_file_path(file_path)
    return build_file_response(resolved_path, original_file_name, download=download)


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
