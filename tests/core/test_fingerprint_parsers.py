from __future__ import annotations

from core.fingerprint.parsers import parse_amount, parse_percentage, parse_period_to_days


class TestParsePeriodToDays:
    def test_simple_days(self) -> None:
        assert parse_period_to_days("90 יום") == 90

    def test_simple_days_plural(self) -> None:
        assert parse_period_to_days("תקופת ההמתנה היא 10 ימים, המתחילה מקרות מקרה הביטוח.") == 10

    def test_months(self) -> None:
        assert parse_period_to_days("תקופת ההמתנה היא 3 חודשים החל מיום שבו המבוטח הפך בלתי כשר") == 90

    def test_years(self) -> None:
        assert parse_period_to_days("2 שנים תקופת אכשרה להישנות מחלת סרטן לאחר החלמה מלאה") == 730

    def test_weeks(self) -> None:
        assert parse_period_to_days("תקופת המתנה נדרשת רק במקרה של שבץ מוחי - 6 שבועות.") == 42

    def test_hours_rounds_to_days(self) -> None:
        assert parse_period_to_days("תרדמת - 96 שעות") == 4

    def test_first_of_several_periods_wins(self) -> None:
        text = "90 יום למקרה ביטוח ראשון; 365 ימים למקרה ביטוח נוסף; 5 שנים ממועד ההחלמה המלאה מסרטן."
        assert parse_period_to_days(text) == 90

    def test_none_when_no_pattern(self) -> None:
        assert parse_period_to_days("אין.") is None

    def test_none_for_empty_or_none(self) -> None:
        assert parse_period_to_days(None) is None
        assert parse_period_to_days("") is None


class TestParseAmount:
    def test_shekel_symbol_prefix(self) -> None:
        assert parse_amount("₪25,000 מקסימום השתתפות עצמית בניתוח") == (25000.0, "ILS")

    def test_shekel_word_suffix(self) -> None:
        assert parse_amount('תקרת השתל 15,000 ש"ח') == (15000.0, "ILS")

    def test_usd_word(self) -> None:
        assert parse_amount('עד 100,000 דולר (דולר אמריקאי) להוצאות רפואיות') == (100000.0, "USD")

    def test_units_are_not_currency(self) -> None:
        # Migdal's "יחידות סכום ביטוח" needs an external multiplier - must
        # NOT be parsed as a bare shekel amount.
        assert parse_amount("600 יחידות סכום ביטוח למקרה גילוי מחלות קשות") is None

    def test_none_when_no_amount(self) -> None:
        assert parse_amount("סכום הביטוח מפורט בדף פרטי הביטוח") is None

    def test_none_for_empty_or_none(self) -> None:
        assert parse_amount(None) is None
        assert parse_amount("") is None


class TestParsePercentage:
    def test_simple_percentage(self) -> None:
        assert parse_percentage("פיצוי בגין סרטן מוקדם בשיעור 20% מסכום הביטוח") == 20.0

    def test_none_when_no_percentage(self) -> None:
        assert parse_percentage("סכום קבוע בדף פרטי הביטוח") is None

    def test_none_for_empty_or_none(self) -> None:
        assert parse_percentage(None) is None
        assert parse_percentage("") is None
