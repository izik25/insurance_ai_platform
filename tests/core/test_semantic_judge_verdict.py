from __future__ import annotations

import pytest

from core.matching.semantic_judge import JudgeVerdict


@pytest.mark.parametrize(
    ("event", "payment", "expected"),
    [
        ("same", "same", "same_coverage"),
        ("narrower_in_b", "same", "different_coverage"),
        ("narrower_in_a", "same", "different_coverage"),
        ("different", "same", "different_coverage"),
        ("same", "different", "different_coverage"),
        ("same", "unclear", "uncertain"),
        ("unclear", "same", "uncertain"),
    ],
)
def test_verdict_is_derived_from_core_axes(event: str, payment: str, expected: str) -> None:
    verdict = JudgeVerdict.model_validate_json(
        '{"core_insured_event": "%s", "core_payment_mechanism": "%s", '
        '"reasoning": "r", "material_differences": []}' % (event, payment)
    )
    assert verdict.verdict == expected


def test_model_supplied_verdict_is_overridden() -> None:
    """Term differences (ages, periods) must never turn into a verdict on
    their own - only the two core axes decide."""
    verdict = JudgeVerdict(
        core_insured_event="same",
        core_payment_mechanism="same",
        verdict="different_coverage",
        reasoning="גיל סיום שונה",
    )
    assert verdict.verdict == "same_coverage"


def test_vague_material_differences_are_dropped() -> None:
    from core.matching.semantic_judge import JudgeVerdict

    verdict = JudgeVerdict(
        core_insured_event="same",
        core_payment_mechanism="same",
        reasoning="r",
        material_differences=[
            {"topic": "חריגים", "document_a": "רשימה מפורטת", "document_b": "רשימה דומה, אך ייתכנו הבדלים דקים בניסוח"},
            {"topic": "גיל סיום", "document_a": "85", "document_b": "75"},
        ],
    )
    assert [d.topic for d in verdict.material_differences] == ["גיל סיום"]


def test_hedged_and_mutually_speculative_differences_are_dropped() -> None:
    from core.matching.semantic_judge import is_vague_difference

    assert is_vague_difference("נדרש רופא מומחה, אך לא צוין במפורש שהרופא מישראל.", "נדרש רופא מומחה בישראל.")
    assert is_vague_difference("ייתכנו מקרים בהם לא יוחרג", "לא פורטו מקרים, פרט לאפשרות פרשנות")
    assert not is_vague_difference("לא מוזכר בנספח (ייתכן מגבלה כללית)", "הכיסוי מסתיים בגיל 75")
    assert not is_vague_difference("85", "75")
