"""Pydantic schema for one document's question-bank answer batch."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Must match document_question_answers.status semantics exactly: FOUND means
# the document explicitly answers the question; NOT_FOUND means the
# document is silent on it (absence noted, not assumed); NOT_APPLICABLE
# means the question doesn't apply to this coverage type at all;
# AMBIGUOUS means the model could not confidently determine the answer.
# Never collapse these into a single "no answer".
ANSWER_STATUSES = {"FOUND", "NOT_FOUND", "NOT_APPLICABLE", "AMBIGUOUS"}


class QuestionAnswerItem(BaseModel):
    question_id: str
    status: str
    answer_text: str | None = None
    evidence_text: str | None = None
    evidence_page: int | None = None
    evidence_section: str | None = None


class AdditionalFindingItem(BaseModel):
    finding_text: str
    related_field: str | None = None
    evidence_page: int | None = None


class QuestionAnswerBatch(BaseModel):
    answers: list[QuestionAnswerItem]
    additional_findings: list[AdditionalFindingItem] = Field(default_factory=list)
