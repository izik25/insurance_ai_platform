"""Loader for the versioned taxonomy config - mirrors core/plugins/registry.py's
explicit-lookup style, but for a data file instead of discovered plugin code."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from core.exceptions import ConfigurationError
from core.taxonomy.schema import TaxonomyCategory, TaxonomyConfig

_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_VERSION = "v1"


@lru_cache
def get_taxonomy(version: str = DEFAULT_VERSION) -> TaxonomyConfig:
    path = _DATA_DIR / f"taxonomy.{version}.yaml"
    if not path.exists():
        raise ConfigurationError(f"Taxonomy config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TaxonomyConfig.model_validate(raw)


def list_leaf_categories(version: str = DEFAULT_VERSION) -> list[TaxonomyCategory]:
    return get_taxonomy(version).categories


def get_category(category_id: str, version: str = DEFAULT_VERSION) -> TaxonomyCategory:
    for category in get_taxonomy(version).categories:
        if category.category_id == category_id:
            return category
    raise ConfigurationError(f"Unknown category_id '{category_id}' in taxonomy {version}")


def parent_chain(category_id: str, version: str = DEFAULT_VERSION) -> list[str]:
    """Return [main_category, coverage_family, coverage_subtype?, coverage_variant?]."""
    category = get_category(category_id, version)
    chain = [category.main_category, category.coverage_family]
    if category.coverage_subtype:
        chain.append(category.coverage_subtype)
    if category.coverage_variant:
        chain.append(category.coverage_variant)
    return chain
