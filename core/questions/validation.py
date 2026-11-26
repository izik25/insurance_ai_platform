"""Rule-based check of question-bank answers against the document text the
model was given - pure code, no LLM.

A FOUND answer is downgraded to AMBIGUOUS when it cites a number that does
not appear anywhere in the document (confirmed live: a scanned Hachshara
appendix whose OCR read "0? יום" got a confident "90 יום" answer - a
plausible guess, but a guess), or when its evidence quote has too little
overlap with the document to be a real quote. A FOUND deductible answer
that only quotes a debt set-off or an "if any" mention (no amount) becomes
NOT_FOUND - that clause is not a deductible. The reason is returned per
question so callers can persist it alongside the answer; nothing is
dropped.
"""

from __future__ import annotations

import re

from core.fingerprint.parsers import normalize_number, numbers_mentioned
from core.questions.schema import QuestionAnswerBatch

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[א-ת]{2,}|\d+")
_HEBREW_PREFIXES = "והבכלמש"

# Numbers this small appear in nearly every document (list numbering,
# "1 בינואר") and carry no signal either way.
_MIN_CHECKED_NUMBER = 3
# A real quote may differ from noisy OCR in a few words (the model fixes
# obvious OCR typos); a fabricated one shares almost nothing with it.
_MIN_EVIDENCE_OVERLAP = 0.5
_MIN_EVIDENCE_WORDS = 4

# A quote saying the insured must stay alive for N days after the insured
# event is a survival period, whatever the answer calls it (confirmed live:
# Harel 541's "waiting period" answer quoted "שהמבוטח חי תקופה העולה על
# יום 14 לאחר קרות מקרה הביטוח" - the same clause as its survival answer).
STAYS_ALIVE = re.compile(
    r"לא נפטר|לא נפטרה|בחיים|חי בתום|חיה בתום|יישאר|תישאר"
    r"|(?:המבוטח|המבוטחת)\s+(?:חי|חיה|יהיה|תהיה)\s"
    r"|(?:חי|חיה)\s+(?:תקופה|במשך|לפחות)"
)
_DEDUCTIBLE_QUESTION = "base.deductible_exists"
# Clauses an answer has mistaken for a deductible (client review 2026-11):
# Harel 481's "החברה רשאית לנכות מסכום הביטוח ... כל חוב" is a debt set-off,
# Harel 466's "גובה ההשתתפות העצמית (אם קיימת)" is indexation boilerplate.
_NOT_A_DEDUCTIBLE = re.compile(r"לנכות|לקזז|קיזוז|כל חוב|ניכוי חוב|אם קיימת|אם קיים\b|אם ישנה|אם יש")
# A real deductible clause states its size.
_AMOUNT_OR_PERCENT = re.compile(r"\d[\d,.]*\s*(?:₪|ש\"ח|ש״ח|שקל|%|אחוז|ימים|יום)|(?:₪|ש\"ח|ש״ח)\s*\d")
_AMOUNT_QUESTIONS = {"base.insurance_amount_details", "base.insurance_amount_exists"}
_DEDUCTIBLE_WORD = re.compile(r"השתתפות(?:\s+ה?עצמית)?")
_NO_DEDUCTIBLE = re.compile(r"(?:ללא|אין|לא כולל|בלי)\s+השתתפות")
_PERIOD_DETAIL_QUESTIONS = {
    "base.waiting_period_details": re.compile(r"המתנה|המתנת"),
    "base.qualifying_period_details": re.compile(r"אכשרה"),
}


def _strip_prefix(word: str) -> str:
    return word[1:] if len(word) > 3 and word[0] in _HEBREW_PREFIXES else word


def _words(text: str) -> set[str]:
    return {_strip_prefix(w) for w in _WORD.findall(text)}


def validate_answers(batch: QuestionAnswerBatch, document_text: str) -> dict[str, str]:
    """Downgrades unsupported FOUND answers to AMBIGUOUS in place and returns
    {question_id: reason} for each one it changed."""
    doc_numbers = numbers_mentioned(document_text)
    doc_words = _words(document_text)
    reasons: dict[str, str] = {}

    for answer in batch.answers:
        if answer.status != "FOUND":
            continue

        missing = [
            n
            for n in (normalize_number(m) for m in _NUMBER.findall(answer.answer_text or ""))
            if n not in doc_numbers and _is_checked(n)
        ]
        if missing:
            reasons[answer.question_id] = (
                f"downgraded FOUND->AMBIGUOUS: number(s) {', '.join(missing)} not in document text"
            )
            answer.status = "AMBIGUOUS"
            continue

        evidence = answer.evidence_text or ""
        if answer.question_id == _DEDUCTIBLE_QUESTION:
            stated = f"{answer.answer_text or ''} {evidence}"
            if _NOT_A_DEDUCTIBLE.search(stated) and not _AMOUNT_OR_PERCENT.search(stated):
                reasons[answer.question_id] = (
                    "changed FOUND->NOT_FOUND: the quoted clause is a debt set-off or an "
                    "'if any' mention, not a deductible the appendix sets"
                )
                answer.status = "NOT_FOUND"
                continue

        amount_text = answer.answer_text or ""
        if (
            answer.question_id in _AMOUNT_QUESTIONS
            and _DEDUCTIBLE_WORD.search(amount_text)
            and not _NO_DEDUCTIBLE.search(amount_text)
        ):
            # Clal 4154 / AIG surgeries: "the 5,000 ₪ deductible is the sum insured".
            reasons[answer.question_id] = (
                "downgraded FOUND->AMBIGUOUS: the answer gives the deductible as the sum insured"
            )
            answer.status = "AMBIGUOUS"
            continue

        own_marker = _PERIOD_DETAIL_QUESTIONS.get(answer.question_id)
        if own_marker and STAYS_ALIVE.search(evidence) and not own_marker.search(evidence):
            reasons[answer.question_id] = (
                "downgraded FOUND->AMBIGUOUS: the quoted clause requires the insured to stay "
                "alive - a survival period, not this period"
            )
            answer.status = "AMBIGUOUS"
            continue

        evidence_words = _words(evidence)
        if len(evidence_words) >= _MIN_EVIDENCE_WORDS:
            overlap = len(evidence_words & doc_words) / len(evidence_words)
            if overlap < _MIN_EVIDENCE_OVERLAP:
                reasons[answer.question_id] = (
                    f"downgraded FOUND->AMBIGUOUS: evidence quote not found in document text "
                    f"(word overlap {overlap:.0%})"
                )
                answer.status = "AMBIGUOUS"

    return reasons


def _is_checked(number: str) -> bool:
    try:
        return float(number) >= _MIN_CHECKED_NUMBER
    except ValueError:
        return False
