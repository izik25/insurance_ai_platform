"""Pydantic schema for one document's taxonomy classification result."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    # Constrained to the known taxonomy category_ids at JSON-schema-build
    # time (see llm_classify._classification_json_schema) - the model can
    # never invent a category_id that doesn't exist in taxonomy.v*.yaml.
    category_id: str
    confidence: float | None = None
    evidence: str | None = None
    # Other category_ids this document could also plausibly belong to -
    # kept distinct from the taxonomy's own static
    # TaxonomyCategory.alternative_categories (that's "these categories are
    # similar in general"; this is "this specific document was ambiguous
    # between these").
    alternative_category_ids: list[str] = Field(default_factory=list)
