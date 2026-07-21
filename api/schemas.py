"""Response models for the dashboard API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    company_id: str
    domain: str
    original_file_name: str
    appendix_number: list[str]
    appendix_name: str | None
    pages_count: int | None
    extraction_method: str
    has_extraction: bool
    has_embedding: bool
    created_date: datetime


class PolicyTableOut(BaseModel):
    title: str | None
    headers: list[str]
    rows: list[list[str]]


class ExtractionOut(BaseModel):
    document_id: str
    appendix_number: list[str]
    appendix_name: str | None
    coverage_type: str | None
    coverage_name: str | None
    eligibility_conditions: str | None
    insurance_amounts: list[str]
    qualifying_period: str | None
    waiting_period: str | None
    exclusions: list[str]
    age_range: str | None
    restrictions: list[str]
    tables: list[PolicyTableOut]
    disease_count: int | None
    disease_list: list[str]
    survival_period: str | None
    created_date: datetime


class MatchDocumentSummary(BaseModel):
    id: str
    company_id: str
    domain: str
    original_file_name: str
    appendix_number: list[str]
    appendix_name: str | None


class MatchOut(BaseModel):
    id: str
    document: MatchDocumentSummary
    matched_document: MatchDocumentSummary
    similarity_score: float
    status: str
    created_date: datetime


class MatchStatusUpdate(BaseModel):
    status: Literal["confirmed", "rejected"]
