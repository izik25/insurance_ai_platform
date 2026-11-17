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

    def test_spelled_out_number(self) -> None:
        text = "תקופת ההמתנה היא שלושה חודשים. לא יגיעו פיצויים בעד הזמן שלפני סיום ההמתנה."
        assert parse_period_to_days(text) == 90

    def test_spelled_out_compound_number(self) -> None:
        assert parse_period_to_days("שנים עשר חודשים במקרה ביטוח הקשור בהריון") == 360

    def test_dual_form(self) -> None:
        assert parse_period_to_days("תקופת אכשרה של חודשיים") == 60

    def test_earliest_form_wins_across_digits_and_words(self) -> None:
        assert parse_period_to_days("שלושה חודשים, ולמקרה נוסף 365 ימים") == 90

    def test_zero_from_damaged_ocr_is_none(self) -> None:
        assert parse_period_to_days("תקופת ההמתנת היא בת 00 יום.") is None

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

    def test_shekels_word(self) -> None:
        assert parse_amount("השתתפות עצמית בסך 90 שקלים לכל מפגש") == (90.0, "ILS")

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


def test_reversed_rtl_period_order_is_parsed() -> None:
    """Phoenix 5630 (2026-11 cancer pilot): PDF reading order put the number
    after its unit."""
    from core.fingerprint.parsers import parse_period_to_days

    text = "הימים הראשונים 90 תקופת האכשרה תחל מיום תחילת תוקפה של תכנית זו ועד חלוף"
    assert parse_period_to_days(text) == 90


def test_reversed_fallback_never_beats_normal_order_or_reads_a_year() -> None:
    from core.fingerprint.parsers import parse_period_to_days

    assert parse_period_to_days("תקופה של 30 יום, ימים 90") == 30
    assert parse_period_to_days("בשנה 2024 נכנס לתוקף") is None
