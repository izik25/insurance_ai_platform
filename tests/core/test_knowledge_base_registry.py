from __future__ import annotations

from core.knowledge_base.registry import (
    get_canonical_codes,
    get_canonical_codes_config,
    get_concepts,
    get_question_bank,
    get_questions_for_category,
    normalize_to_code,
)
from core.taxonomy.registry import list_leaf_categories


def test_question_bank_loads() -> None:
    bank = get_question_bank()
    assert bank.version == "v1"
    assert len(bank.base_questions) >= 15


def test_base_question_ids_are_unique() -> None:
    bank = get_question_bank()
    ids = [q.question_id for q in bank.base_questions]
    assert len(ids) == len(set(ids))


def test_category_questions_keyed_by_real_category_ids() -> None:
    bank = get_question_bank()
    known_ids = {c.category_id for c in list_leaf_categories()}
    for category_id in bank.category_questions:
        assert category_id in known_ids


def test_get_questions_for_category_includes_base_and_specific() -> None:
    base_count = len(get_question_bank().base_questions)
    questions = get_questions_for_category("health.critical_illness.cancer")
    assert len(questions) > base_count


def test_get_questions_for_unmapped_category_returns_base_only() -> None:
    base_count = len(get_question_bank().base_questions)
    questions = get_questions_for_category("other.unclassified")
    assert len(questions) == base_count


def test_canonical_codes_load_and_have_unique_codes() -> None:
    codes = get_canonical_codes_config().codes
    assert len(codes) > 0
    code_values = [c.code for c in codes]
    assert len(code_values) == len(set(code_values))


def test_get_canonical_codes_filters_by_category() -> None:
    insured_event_codes = get_canonical_codes("insured_event")
    assert all(c.code_category == "insured_event" for c in insured_event_codes)
    assert len(insured_event_codes) < len(get_canonical_codes())


def test_normalize_to_code_matches_canonical_name() -> None:
    assert normalize_to_code("שבץ מוחי") == "STROKE"


def test_normalize_to_code_matches_synonym() -> None:
    assert normalize_to_code("מחלת הסרטן") == "CANCER"


def test_normalize_to_code_returns_none_for_unknown_phrase() -> None:
    assert normalize_to_code("ביטוח לכיסוי מכשירי חשמל ביתיים") is None


def test_concepts_load() -> None:
    concepts = get_concepts()
    assert concepts.version == "v1"
    assert len(concepts.concepts) > 0
