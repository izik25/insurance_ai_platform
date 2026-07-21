"""Uniform structured-field schema extracted from every policy appendix.

One schema for every company: the whole point is to compare documents by
these fields (and later, by embeddings built from them) regardless of how
each insurer phrases or numbers things. Mirrors `DocumentExtraction` in
`core/database/models.py` field-for-field.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PolicyTable(BaseModel):
    """One table found in the document (e.g. insurance amounts per age band)."""

    title: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class PolicyExtraction(BaseModel):
    """Structured fields for one policy appendix, as extracted by the LLM."""

    appendix_number: list[str] = Field(default_factory=list)
    appendix_name: str | None = None
    coverage_type: str | None = None
    coverage_name: str | None = None
    eligibility_conditions: str | None = None
    insurance_amounts: list[str] = Field(default_factory=list)
    qualifying_period: str | None = None  # תקופת אכשרה
    waiting_period: str | None = None  # תקופת המתנה
    exclusions: list[str] = Field(default_factory=list)
    age_range: str | None = None
    restrictions: list[str] = Field(default_factory=list)
    tables: list[PolicyTable] = Field(default_factory=list)
    disease_count: int | None = None
    disease_list: list[str] = Field(default_factory=list)
    survival_period: str | None = None  # תקופת הישרדות

    def embedding_text(self) -> str:
        """A normalized text summary built from fields, not raw document text.

        Used as the embedding input for cross-company matching: two
        documents describing the same coverage should produce near-identical
        embedding text here even if their source PDFs are phrased totally
        differently, since this pulls only the structured content.
        """
        parts = [
            self.coverage_type,
            self.coverage_name,
            self.eligibility_conditions,
            self.qualifying_period,
            self.waiting_period,
            self.age_range,
            self.survival_period,
            *self.insurance_amounts,
            *self.exclusions,
            *self.restrictions,
            *self.disease_list,
        ]
        return "\n".join(p for p in parts if p)
