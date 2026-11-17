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
