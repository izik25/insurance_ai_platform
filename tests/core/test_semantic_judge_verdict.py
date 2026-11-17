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
