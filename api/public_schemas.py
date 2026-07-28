"""Response models for the public appendix/comparison API (api/public_routes.py)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PublicCompanyOut(BaseModel):
    id: str
    display_name: str
    appendix_count: int


class PublicAppendixSummary(BaseModel):
    company_id: str
    appendix_number: list[str]
    appendix_name: str | None
    domain: str
    pages_count: int | None
    created_date: datetime


class PublicPolicyTableOut(BaseModel):
    title: str | None
    headers: list[str]
    rows: list[list[str]]


class PublicAppendixDetail(BaseModel):
    company_id: str
    appendix_number: list[str]
    appendix_name: str | None
    domain: str
    pages_count: int | None
    has_extraction: bool
    coverage_type: str | None
    coverage_name: str | None
    eligibility_conditions: str | None
    insurance_amounts: list[str]
    qualifying_period: str | None
    waiting_period: str | None
    exclusions: list[str]
    age_range: str | None
    restrictions: list[str]
    tables: list[PublicPolicyTableOut]
    disease_count: int | None
    disease_list: list[str]
    survival_period: str | None
    created_date: datetime


class PublicAppendixMatch(BaseModel):
    company_id: str
    appendix_number: list[str]
    appendix_name: str | None
    domain: str
    similarity_score: float
    status: str
