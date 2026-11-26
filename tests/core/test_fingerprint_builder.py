from __future__ import annotations

from core.canonical.schema import AgeRestrictions, CanonicalCoverageProfile, DeductibleInfo
from core.fingerprint.builder import build_fingerprint


def _profile(**overrides) -> CanonicalCoverageProfile:
    defaults = dict(
        insured_event="גילוי מחלת הסרטן",
        covered_events=["אירוע א", "אירוע ב"],
        exclusions_normalized=["חריג א", "חריג ב", "חריג ג"],
        limitations=[],
        waiting_period_text="90 יום מתחילת הביטוח",
        qualifying_period_text=None,
        survival_period_text="30 יום",
        benefit_type="פיצוי חד פעמי",
        benefit_calculation="20% מסכום הביטוח",
        amounts=["₪500,000 במקרה של אבחון מחלה קשה"],
        caps=['תקרת השתל 15,000 ש"ח'],
        deductible=DeductibleInfo(applies=True, amount_text="₪25,000", description="השתתפות עצמית"),
        age_restrictions=AgeRestrictions(min_age=18, max_age=65, termination_age=70, description="18-65"),
        special_conditions=["תנאי מיוחד א"],
    )
    defaults.update(overrides)
    return CanonicalCoverageProfile(**defaults)


def test_build_fingerprint_derives_periods_and_amounts() -> None:
    result = build_fingerprint(
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype="CANCER",
        benefit_model="LUMP_SUM",
        target_population="INDIVIDUAL",
        profile=_profile(),
        benefit_type_code="LUMP_SUM",
    )
    assert result.waiting_period_days == 90
    assert result.qualifying_period_days is None
    assert result.survival_period_days == 30
    assert result.benefit_amount_min == 500000.0
    assert result.benefit_amount_currency == "ILS"
    assert result.maximum_benefit == 15000.0
    assert result.deductible_amount == 25000.0
    assert result.benefit_percentage == 20.0
    assert result.benefit_type == "LUMP_SUM"
    assert result.min_entry_age == 18
    assert result.max_entry_age == 65
    assert result.termination_age == 70


def test_build_fingerprint_counts_match_profile_lists() -> None:
    result = build_fingerprint(
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=_profile(),
        benefit_type_code=None,
    )
    assert result.covered_event_count == 2
    assert result.major_exclusion_count == 3
    assert result.special_condition_count == 1


def test_build_fingerprint_falls_back_to_raw_benefit_type_when_no_code() -> None:
    result = build_fingerprint(
        main_category=None,
        coverage_family=None,
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=_profile(benefit_type="ניסוח חופשי לא ידוע"),
        benefit_type_code=None,
    )
    assert result.benefit_type == "ניסוח חופשי לא ידוע"


def test_build_fingerprint_handles_no_deductible() -> None:
    result = build_fingerprint(
        main_category=None,
        coverage_family=None,
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=_profile(deductible=DeductibleInfo(applies=False)),
        benefit_type_code=None,
    )
    assert result.deductible_amount is None


def test_build_fingerprint_is_deterministic() -> None:
    profile = _profile()
    kwargs = dict(
        main_category="HEALTH",
        coverage_family="CRITICAL_ILLNESS",
        coverage_subtype="CANCER",
        benefit_model="LUMP_SUM",
        target_population="INDIVIDUAL",
        profile=profile,
        benefit_type_code="LUMP_SUM",
    )
    first = build_fingerprint(**kwargs)
    second = build_fingerprint(**kwargs)
    assert first.as_dict() == second.as_dict()


def _build(profile: CanonicalCoverageProfile, answer_statuses: dict[str, str] | None = None):
    return build_fingerprint(
        main_category="HEALTH",
        coverage_family="SURGERIES",
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=profile,
        benefit_type_code=None,
        answer_statuses=answer_statuses,
    )


def test_survival_condition_in_waiting_slot_moves_to_survival() -> None:
    text = "תקופת המתנה של 14 ימים ממועד קרות מקרה הביטוח, ובלבד שהמבוטח לא נפטר במהלך 14 הימים"
    result = _build(_profile(waiting_period_text=text, survival_period_text=None))
    assert result.waiting_period_days is None
    assert result.survival_period_days == 14
    assert result.raw_features["sanity_adjustments"]


def test_duplicate_period_text_kept_only_where_its_wording_points() -> None:
    text = "90 ימים. מובהר כי לא תהא תקופת אכשרה למקרה ביטוח הנגרם בשל תאונה."
    profile = _profile(
        waiting_period_text=text, qualifying_period_text=text, survival_period_text=None
    )
    result = _build(profile)
    assert result.qualifying_period_days == 90
    assert result.waiting_period_days is None


def test_period_dropped_when_question_bank_found_none() -> None:
    statuses = {
        "base.survival_period_exists": "NOT_FOUND",
        "base.survival_period_details": "NOT_FOUND",
        "base.insured_event": "FOUND",
        "base.covered_scope": "FOUND",
        "base.exclusions": "FOUND",
    }
    text = "פיצוי במקרה פטירת המבוטח בתוך 14 ימים ממועד הניתוח"
    result = _build(_profile(survival_period_text=text), statuses)
    assert result.survival_period_days is None
    assert result.raw_features["is_coverage_document"] is True


def test_deductible_premium_and_per_unit_amounts_excluded_from_range() -> None:
    amounts = [
        "השתתפות עצמית בגין ביצוע ניתוח בסך 5,000 ₪",
        "עלות פוליסה פרמיה חודשית: 5.00 ש\"ח",
        "עד ₪ 250 ליום ועד 60 ימים",
        "עד ₪ 3,000,000 שיפוי עבור תרופה שאינה כלולה בסל",
        "פיצוי פטירה - 150,000 ₪",
    ]
    result = _build(_profile(amounts=amounts))
    assert result.benefit_amount_min == 150000.0
    assert result.benefit_amount_max == 3000000.0
    assert result.amount_raw == amounts


def test_entry_ages_from_premium_bands_are_dropped() -> None:
    ages = AgeRestrictions(
        min_age=0,
        max_age=66,
        termination_age=None,
        description="דמי הביטוח משתנים לפי קבוצות גיל עד גיל 66, מגיל 66 ואילך דמי הביטוח קבועים",
    )
    result = _build(_profile(age_restrictions=ages))
    assert result.min_entry_age is None
    assert result.max_entry_age is None


def test_max_entry_age_equal_to_termination_age_is_dropped() -> None:
    ages = AgeRestrictions(
        min_age=None,
        max_age=65,
        termination_age=65,
        description="הכיסוי פוקע עם הגיע המבוטח לגיל 65",
    )
    result = _build(_profile(age_restrictions=ages))
    assert result.max_entry_age is None
    assert result.termination_age == 65


def test_explicit_entry_limit_is_kept() -> None:
    ages = AgeRestrictions(
        min_age=18, max_age=65, termination_age=75, description="גיל הצטרפות 18 עד 65"
    )
    result = _build(_profile(age_restrictions=ages))
    assert (result.min_entry_age, result.max_entry_age) == (18, 65)


def test_document_with_almost_no_answers_is_flagged_non_coverage() -> None:
    statuses = {"base.termination_conditions": "FOUND", "base.insured_event": "NOT_FOUND"}
    result = _build(_profile(), statuses)
    assert result.raw_features["is_coverage_document"] is False


def _gated(profile: CanonicalCoverageProfile, statuses: dict[str, str], texts: dict):
    return build_fingerprint(
        main_category="HEALTH",
        coverage_family="SURGERIES",
        coverage_subtype=None,
        benefit_model=None,
        target_population=None,
        profile=profile,
        benefit_type_code=None,
        answer_statuses=statuses,
        answer_texts=texts,
    )


def test_payment_on_death_clause_is_not_a_survival_period() -> None:
    evidence = "נפטר המבוטח בתוך 14 ימים ממועד ביצוע הניתוח, תשלם החברה פיצוי בגובה 150,000 ₪"
    statuses = {"base.survival_period_details": "FOUND"}
    texts = {"base.survival_period_details": ("תקופת הישרדות היא 14 ימים", evidence)}
    profile = _profile(waiting_period_text=None, survival_period_text="14 ימים ממועד הניתוח")
    assert _gated(profile, statuses, texts).survival_period_days is None


def test_period_value_must_match_question_bank_answer() -> None:
    statuses = {"base.waiting_period_details": "AMBIGUOUS"}
    texts = {"base.waiting_period_details": ("לא ברור (0? יום)", "במשך תקופה של 0? יום")}
    profile = _profile(
        waiting_period_text="30 יום מהיום שבו הודיעה החברה", survival_period_text=None
    )
    assert _gated(profile, statuses, texts).waiting_period_days is None


def test_period_supported_by_answer_in_other_units_is_kept() -> None:
    statuses = {"base.waiting_period_details": "FOUND"}
    texts = {"base.waiting_period_details": ("3 חודשים", "תקופת ההמתנה היא שלושה חודשים")}
    profile = _profile(
        waiting_period_text="תקופת ההמתנה היא שלושה חודשים", survival_period_text=None
    )
    assert _gated(profile, statuses, texts).waiting_period_days == 90


def test_entry_ages_dropped_when_quote_is_a_price_table() -> None:
    ages = AgeRestrictions(min_age=18, max_age=19, description="מגבלת גיל להצטרפות 18 עד 19")
    statuses = {"base.age_limitations": "FOUND"}
    quote = "ילד עד גיל 18 5.00 שקלים חדשים, מגיל 19"
    texts = {"base.age_limitations": ("גיל הצטרפות 18-19", quote)}
    result = _gated(_profile(age_restrictions=ages), statuses, texts)
    assert (result.min_entry_age, result.max_entry_age) == (None, None)


def test_entry_ages_kept_when_quote_states_entry_limit() -> None:
    ages = AgeRestrictions(min_age=18, max_age=65, description="גיל כניסה 18 עד 65")
    statuses = {"base.age_limitations": "FOUND"}
    texts = {"base.age_limitations": ("18 עד 65", "גיל הכניסה לביטוח הוא מגיל 18 ועד גיל 65")}
    result = _gated(_profile(age_restrictions=ages), statuses, texts)
    assert (result.min_entry_age, result.max_entry_age) == (18, 65)


def test_survival_clause_quoted_as_waiting_period_is_dropped() -> None:
    """Harel 541 (2026-11 cancer pilot): the waiting-period answer quoted the
    survival clause, so waiting=14 duplicated the 14-day survival period."""
    quote = "החברה תשלם את סכום הביטוח כאשר המבוטח חי תקופה העולה על יום 14 לאחר קרות מקרה הביטוח"
    statuses = {"base.waiting_period_details": "FOUND", "base.survival_period_details": "FOUND"}
    texts = {
        "base.waiting_period_details": ("תקופת ההמתנה היא 14 ימים", quote),
        "base.survival_period_details": ("תקופת ההישרדות היא 14 יום", quote),
    }
    profile = _profile(waiting_period_text="14 ימים מקרות מקרה הביטוח", survival_period_text="14 ימים")
    result = _gated(profile, statuses, texts)
    assert result.waiting_period_days is None
    assert result.survival_period_days == 14


def test_sub_limits_examples_and_deductible_figure_excluded() -> None:
    # Client review 2026-11: Clal 2670 / Direct 216/02 / Harel 466 / Clal 4154.
    amounts = [
        'פיצוי עד 50,000 ש"ח בגין מקרה סרטן מוקדם',
        "פיצוי עבור מחלת סכרת נעורים מוגבל לתקרה של 300,000 ₪.",
        "פיצוי חד פעמי נוסף של 10% מסכום הביטוח ועד 20,000 ₪ לצינתור כלילי טיפולי",
        "סכום ביטוח שנרכש, לדוגמה 10,000 ₪",
        "₪ 25,000 סכום מקסימלי לתביעות עבור ניתוח פרטי בישראל",
    ]
    caps = ["לא יותר מ- 15% מתגמולי הביטוח ולא יותר מ- ₪ 50,000", "תקרה כוללת 400,000 ₪"]
    result = _build(_profile(amounts=amounts, caps=caps))
    assert result.benefit_amount_min is None
    assert result.benefit_amount_max is None
    assert result.maximum_benefit == 400000.0
    assert result.amount_raw == amounts
    assert sum("sub-limit" in a for a in result.raw_features["sanity_adjustments"]) == 5
    assert any("same figure as the deductible" in a for a in result.raw_features["sanity_adjustments"])


def test_review_round1_period_and_age_guards() -> None:
    # Survival worded as non-payment on death (AIG Extra Care) is kept.
    survival = "המבטח לא ישלם תגמולי ביטוח אם נפטר המבוטח בתוך 7 ימים ממועד האבחון"
    statuses = {"base.survival_period_details": "FOUND", "base.age_limitations": "FOUND",
                "base.waiting_period_details": "FOUND"}
    texts = {
        "base.survival_period_details": ("7 ימים", "אם... נפטר המבוטח בתוך 7 ימים ממועד האבחון לא ישלם המבטח"),
        "base.waiting_period_details": ("5 שנים", "תגמולי אבחון עבור סרטן חוזר לאחר 5 שנים"),
        "base.age_limitations": ("", "מחלה שהתגלתה לראשונה לאחר הצטרפותו של המבוטח לפוליסה ולפני הגיעו לגיל 3"),
    }
    profile = _profile(
        survival_period_text=survival,
        waiting_period_text="תגמולי אבחון עבור סרטן חוזר לאחר 5 שנים (60 חודשים) ממועד האבחנה הקודמת",
        age_restrictions=AgeRestrictions(min_age=3, termination_age=75, description="הצטרפות"),
    )
    result = build_fingerprint(
        main_category="HEALTH", coverage_family=None, coverage_subtype=None, benefit_model=None,
        target_population=None, profile=profile, benefit_type_code=None,
        answer_statuses=statuses, answer_texts=texts,
    )
    assert result.survival_period_days == 7
    assert result.waiting_period_days is None
    assert result.min_entry_age is None
    # The age quote never ends the coverage at 75.
    assert result.termination_age is None


def test_condition_specific_waiting_period_keeps_text_not_days() -> None:
    text = 'שבץ מוחי: חייבת להתקיים עדות לנזק נוירולוגי הנמשך מעל 6 שבועות. תקופה זו מהווה תקופת המתנה.'
    result = _build(_profile(waiting_period_text=text))
    assert result.waiting_period_days is None
    assert result.waiting_period_raw == text
