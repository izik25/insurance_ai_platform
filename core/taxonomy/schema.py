"""Pydantic schema for the versioned taxonomy config file."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaxonomyCategory(BaseModel):
    """One leaf (or near-leaf) node of the taxonomy tree.

    The tree structure itself (main_category -> coverage_family ->
    coverage_subtype -> coverage_variant) is expressed as parallel string
    fields rather than nesting, so a document's classification is a flat
    row (see core/classification/schema.py) and category_id is the single
    stable join key everywhere else (question bank, matching profiles,
    candidate index).
    """

    category_id: str
    main_category: str
    coverage_family: str
    coverage_subtype: str | None = None
    coverage_variant: str | None = None
    benefit_model: str | None = None
    target_population: str | None = None
    display_name_he: str
    description_he: str | None = None
    # Other category_ids a document classified here could plausibly also
    # belong to - kept so an ambiguous classification doesn't silently drop
    # a real candidate from matching (req: "אל תסתפק בדוגמאות... אם הסיווג
    # אינו חד-משמעי, אפשר לשמור גם alternative_categories").
    alternative_categories: list[str] = Field(default_factory=list)


class TaxonomyConfig(BaseModel):
    version: str
    categories: list[TaxonomyCategory]
