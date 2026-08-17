"""Loader for per-category matching profiles, falling back to default.v*.yaml -
mirrors CompanyRegistry.get()'s explicit-lookup-with-fallback pattern
(core/plugins/registry.py)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from core.exceptions import ConfigurationError
from core.matching.profiles.schema import CategoryMatchingProfile

_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_VERSION = "v1"


@lru_cache
def _load_profile_file(filename: str) -> CategoryMatchingProfile:
    path = _DATA_DIR / filename
    if not path.exists():
        raise ConfigurationError(f"Matching profile not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CategoryMatchingProfile.model_validate(raw)


def get_profile_for_category(
    category_id: str, version: str = DEFAULT_VERSION
) -> CategoryMatchingProfile:
    specific = _DATA_DIR / f"{category_id}.{version}.yaml"
    if specific.exists():
        return _load_profile_file(specific.name)
    return _load_profile_file(f"default.{version}.yaml")
