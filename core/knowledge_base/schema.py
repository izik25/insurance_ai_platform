"""Pydantic schemas for the question bank, canonical codes, and concepts config files."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuestionDef(BaseModel):
    question_id: str
    text_he: str
    # What shape an answer takes - guides both the LLM structured-output
    # schema built at question-answering time and any later rule-based
    # validation of the answer, without hardcoding either here.
    expected_type: str = "text"  # text | boolean | number | list | enum


class QuestionBankConfig(BaseModel):
    version: str
    base_questions: list[QuestionDef]
    # category_id -> questions unique to that category. Not every taxonomy
    # leaf needs an entry; categories without one get base_questions only.
    category_questions: dict[str, list[QuestionDef]] = Field(default_factory=dict)


class CanonicalCode(BaseModel):
    code: str
    # Which fingerprint/profile dimension this code belongs to - matches
    # document_canonical_codes.code_category in the DB design.
    code_category: str  # insured_event | covered_event | exclusion | limitation | eligibility | definition | claim_requirement | extension
    canonical_name_he: str
    synonyms: list[str] = Field(default_factory=list)


class CanonicalCodesConfig(BaseModel):
    version: str
    codes: list[CanonicalCode]


class ConceptEntry(BaseModel):
    concept_id: str
    name_he: str
    synonyms: list[str] = Field(default_factory=list)
    related_concepts: list[str] = Field(default_factory=list)
    definition_he: str | None = None


class ConceptsConfig(BaseModel):
    version: str
    concepts: list[ConceptEntry]
