from __future__ import annotations

from companies.migdal.config import classify_department


def test_health_department_classified() -> None:
    assert classify_department("ביטוח בריאות וסיעוד") == "health"
    assert classify_department("אובדן כושר עבודה") == "health"


def test_life_department_classified() -> None:
    assert classify_department("ביטוח חיים עם חיסכון") == "life"
    assert classify_department("ביטוח למקרה מוות") == "life"


def test_mixed_department_classified() -> None:
    assert classify_department("קולקטיבים") == "mixed"


def test_unrelated_department_excluded() -> None:
    assert classify_department("חיסכון אישי") is None
    assert classify_department("ביטוח רכב") is None
