from __future__ import annotations

from core.questions.schema import QuestionAnswerBatch, QuestionAnswerItem
from core.questions.validation import validate_answers

_DOCUMENT = (
    "במקרה של גילוי מחלת הסרטן במשך תקופה של 0? יום החל מיום תחילת הביטוח לא תהיה לחברה אחריות. "
    "תקופת ההמתנה היא שלושה חודשים. סכום הביטוח 150,000 ש\"ח."
)


def _batch(*answers: QuestionAnswerItem) -> QuestionAnswerBatch:
    return QuestionAnswerBatch(answers=list(answers))


def test_number_missing_from_document_is_downgraded() -> None:
    document = (
        "במקרה של גילוי מחלת הסרטן במשך תקופה של 0? יום החל מיום תחילת הביטוח "
        "לא תהיה לחברה אחריות."
    )
    batch = _batch(
        QuestionAnswerItem(
            question_id="base.qualifying_period_details",
            status="FOUND",
            answer_text="תקופת האכשרה היא 90 יום מתחילת הביטוח.",
            evidence_text="במשך תקופה של 90 יום החל מיום תחילת הביטוח",
        )
    )
    reasons = validate_answers(batch, document)
    assert batch.answers[0].status == "AMBIGUOUS"
    assert "90" in reasons["base.qualifying_period_details"]


def test_converted_and_spelled_out_numbers_are_accepted() -> None:
    batch = _batch(
        QuestionAnswerItem(
            question_id="base.waiting_period_details",
            status="FOUND",
            answer_text="3 חודשים (90 יום)",
            evidence_text="תקופת ההמתנה היא שלושה חודשים",
        ),
        QuestionAnswerItem(
            question_id="base.insurance_amount_details",
            status="FOUND",
            answer_text="150000 ש\"ח",
            evidence_text="סכום הביטוח 150,000 ש\"ח",
        ),
    )
    assert validate_answers(batch, _DOCUMENT) == {}
    assert all(a.status == "FOUND" for a in batch.answers)


def test_paraphrased_evidence_is_downgraded() -> None:
    batch = _batch(
        QuestionAnswerItem(
            question_id="base.special_definitions",
            status="FOUND",
            answer_text="יש הגדרות מיוחדות",
            evidence_text=(
                "סעיף 2 הגדרות מפרט הגדרות מיוחדות למונחים שונים המשפיעים על משמעות הכיסוי"
            ),
        )
    )
    validate_answers(batch, _DOCUMENT)
    assert batch.answers[0].status == "AMBIGUOUS"


def test_non_found_answers_are_left_alone() -> None:
    batch = _batch(QuestionAnswerItem(question_id="base.cap_exists", status="NOT_FOUND"))
    assert validate_answers(batch, _DOCUMENT) == {}
    assert batch.answers[0].status == "NOT_FOUND"


def test_waiting_period_quoting_a_survival_clause_is_downgraded() -> None:
    document = "החברה תשלם את סכום הביטוח כאשר המבוטח חי תקופה העולה על יום 14 לאחר קרות מקרה הביטוח."
    batch = _batch(
        QuestionAnswerItem(
            question_id="base.waiting_period_details",
            status="FOUND",
            answer_text="תקופת ההמתנה היא 14 ימים מקרות מקרה הביטוח",
            evidence_text="כאשר המבוטח חי תקופה העולה על יום 14 לאחר קרות מקרה הביטוח",
        ),
        QuestionAnswerItem(
            question_id="base.survival_period_details",
            status="FOUND",
            answer_text="תקופת ההישרדות היא 14 יום",
            evidence_text="כאשר המבוטח חי תקופה העולה על יום 14 לאחר קרות מקרה הביטוח",
        ),
    )
    reasons = validate_answers(batch, document)
    assert batch.answers[0].status == "AMBIGUOUS"
    assert "survival" in reasons["base.waiting_period_details"]
    assert batch.answers[1].status == "FOUND"


def test_real_waiting_period_mentioning_survival_word_is_kept() -> None:
    document = "תקופת המתנה של 90 יום מקרות מקרה הביטוח, ובלבד שהמבוטח בחיים בתומה."
    batch = _batch(
        QuestionAnswerItem(
            question_id="base.waiting_period_details",
            status="FOUND",
            answer_text="90 יום",
            evidence_text="תקופת המתנה של 90 יום מקרות מקרה הביטוח, ובלבד שהמבוטח בחיים בתומה",
        )
    )
    validate_answers(batch, document)
    assert batch.answers[0].status == "FOUND"


def test_debt_set_off_and_if_any_mention_are_not_a_deductible() -> None:
    # Client review 2026-11: Harel 481 (set-off) and Harel 466 (indexation boilerplate).
    document = (
        "החברה רשאית לנכות מסכום הביטוח המשולם למוטב, כל חוב המגיע לה מהמבוטח על פי פוליסה זו. "
        "כל הסכומים הנקובים בפוליסה זו, לרבות סכומי הביטוח, הפרמיות וגובה ההשתתפות העצמית (אם קיימת) "
        "צמודים למדד. השתתפות עצמית בגובה 5,000 ₪ למקרה ביטוח."
    )
    set_off = QuestionAnswerItem(
        question_id="base.deductible_exists",
        status="FOUND",
        answer_text="קיימת השתתפות עצמית עקב חוב שניתן לנכות מסכום הביטוח.",
        evidence_text="החברה רשאית לנכות מסכום הביטוח המשולם למוטב, כל חוב המגיע לה מהמבוטח",
    )
    batch = _batch(set_off)
    reasons = validate_answers(batch, document)
    assert batch.answers[0].status == "NOT_FOUND"
    assert "set-off" in reasons["base.deductible_exists"]

    boilerplate = _batch(QuestionAnswerItem(
        question_id="base.deductible_exists",
        status="FOUND",
        answer_text="במסמך מצוין כי קיימת השתתפות עצמית הצמודה למדד.",
        evidence_text="וגובה ההשתתפות העצמית (אם קיימת) צמודים למדד",
    ))
    validate_answers(boilerplate, document)
    assert boilerplate.answers[0].status == "NOT_FOUND"

    real = _batch(QuestionAnswerItem(
        question_id="base.deductible_exists",
        status="FOUND",
        answer_text="קיימת השתתפות עצמית של 5,000 ₪ לכל מקרה ביטוח.",
        evidence_text="השתתפות עצמית בגובה 5,000 ₪ למקרה ביטוח",
    ))
    assert validate_answers(real, document) == {}
    assert real.answers[0].status == "FOUND"


def test_amount_answer_quoting_the_deductible_is_downgraded() -> None:
    document = "כיסוי מלא בניכוי השתתפות עצמית למקרה ביטוח בגובה 5,000 ₪. סכום הביטוח 150,000 ש\"ח."
    batch = _batch(
        QuestionAnswerItem(
            question_id="base.insurance_amount_details",
            status="FOUND",
            answer_text="סכום ההשתתפות העצמית הוא 5,000 ₪, זהו סכום הביטוח.",
            evidence_text="בניכוי השתתפות עצמית למקרה ביטוח בגובה 5,000 ₪",
        ),
        QuestionAnswerItem(
            question_id="base.insurance_amount_exists",
            status="FOUND",
            answer_text="כן, 150,000 ש\"ח ללא השתתפות עצמית.",
            evidence_text="סכום הביטוח 150,000 ש\"ח",
        ),
    )
    reasons = validate_answers(batch, document)
    assert batch.answers[0].status == "AMBIGUOUS"
    assert "deductible" in reasons["base.insurance_amount_details"]
    assert batch.answers[1].status == "FOUND"


def test_explicit_no_deductible_in_document_is_an_answer() -> None:
    document = "ניתוחים וטיפולים מחליפי הניתוח בישראל (ניתוחים משלים שב\"ן ללא השתתפות עצמית) 535 נספח מספר"
    batch = _batch(QuestionAnswerItem(question_id="base.deductible_exists", status="NOT_FOUND"))
    reasons = validate_answers(batch, document)
    assert batch.answers[0].status == "FOUND"
    assert "אין השתתפות עצמית" in batch.answers[0].answer_text
    assert "ללא השתתפות עצמית" in batch.answers[0].evidence_text
    assert "base.deductible_exists" in reasons
