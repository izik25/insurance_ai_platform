from __future__ import annotations

import pytest

from core.exceptions import ConfigurationError
from core.taxonomy.registry import get_category, get_taxonomy, list_leaf_categories, parent_chain


def test_taxonomy_loads_and_validates() -> None:
    config = get_taxonomy()
    assert config.version == "v1"
    assert len(config.categories) > 0


def test_category_ids_are_unique() -> None:
    categories = list_leaf_categories()
    ids = [c.category_id for c in categories]
    assert len(ids) == len(set(ids))


def test_every_main_category_has_an_other_fallback() -> None:
    categories = list_leaf_categories()
    main_categories = {c.main_category for c in categories}
    fallback_families = {
        c.main_category for c in categories if c.coverage_family in {"OTHER", "UNCLASSIFIED"}
    }
    # Every main_category should be reachable by an explicit fallback leaf,
    # so classification never has to leave a document uncategorized.
    assert main_categories <= fallback_families | {"OTHER"}


def test_alternative_categories_reference_real_category_ids() -> None:
    categories = list_leaf_categories()
    ids = {c.category_id for c in categories}
    for category in categories:
        for alt in category.alternative_categories:
            assert alt in ids, f"{category.category_id} references unknown alt category {alt}"


def test_get_category_known_id() -> None:
    category = get_category("health.critical_illness.cancer")
    assert category.main_category == "HEALTH"
    assert category.coverage_family == "CRITICAL_ILLNESS"


def test_get_category_unknown_id_raises() -> None:
    with pytest.raises(ConfigurationError):
        get_category("does.not.exist")


def test_parent_chain() -> None:
    chain = parent_chain("health.surgeries.israel")
    assert chain == ["HEALTH", "SURGERIES", "ISRAEL_SURGERIES"]
