from __future__ import annotations

import pytest

from companies.aig.identifiers import edition_from_title, internal_appendix_id, unique_internal_id


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Extra Care ביטוח למחלות קשות בתוקף החל מ 01.08.2023", "AIG-EXTRACARE-2023.08"),
        ("גילוי נאות extra care בתוקף החל מ-08.2023", "AIG-EXTRACARE-DISC-2023.08"),
        ("פוליסת ניתוחים בישראל משלים שבן עם השתתפות עצמית 5000 בתוקף החל מ 09.2023",
         "AIG-SURGERY-IL-SHABAN-DEDUCTIBLE-2023.09"),
        ("פוליסת ניתוחים בישראל מהשקל הראשון בתוקף החל מ 09.2023", "AIG-SURGERY-IL-FIRST-SHEKEL-2023.09"),
        ("משלים שבן בתוקף החל מ-01.10.2017 AIG Medicare פוליסת", "AIG-MEDICARE-SHABAN-2017.10"),
        ("פוליסת-AIG-MEDICARE-שקל-ראשון-בתוקף-החל-מ-1.9.18ועד ל- 28.01.2019", "AIG-MEDICARE-FIRST-SHEKEL-2018.09"),
        ("פוליסת ביטוח חיים למשכנתא ללקוחות מקס ביטוח בתוקף מיולי 2021", "AIG-MORTGAGE-MAX-2021.07"),
        ("פוליסה לביטוח חיים בתוקף מיוני 14 ועד דצמבר 14", "AIG-LIFE-RISK-2014.06"),
        ("פוליסת ביטוח חיים למשכנתא בתוקף משנת 1999-עד שנת-2001", "AIG-MORTGAGE-1999"),
        ("פוליסת בטחון לחיים", "AIG-BITACHON-LACHAIM"),
        ("גילוי נאות בתוקף מ 15.7.2026", "AIG-GENERAL-DISC-2026.07"),
    ],
)
def test_internal_appendix_id(title: str, expected: str) -> None:
    assert internal_appendix_id(title) == expected


def test_edition_uses_the_first_date_in_the_title() -> None:
    assert edition_from_title("בתוקף החל מ-01.01.2012 ועד ל-31.08.2013") == "2012.01"
    assert edition_from_title("ללא תאריך") is None


def test_unique_internal_id_suffixes_collisions() -> None:
    taken: set[str] = set()
    title = "פוליסת extra care בתוקף החל מ-01.08.2023"
    assert unique_internal_id(title, taken) == "AIG-EXTRACARE-2023.08"
    assert unique_internal_id(title, taken) == "AIG-EXTRACARE-2023.08-2"
    assert unique_internal_id(title, taken) == "AIG-EXTRACARE-2023.08-3"
