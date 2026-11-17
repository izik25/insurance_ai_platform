"""Rule-based check of question-bank answers against the document text the
model was given - pure code, no LLM.

A FOUND answer is downgraded to AMBIGUOUS when it cites a number that does
not appear anywhere in the document (confirmed live: a scanned Hachshara
appendix whose OCR read "0? יום" got a confident "90 יום" answer - a
plausible guess, but a guess), or when its evidence quote has too little
overlap with the document to be a real quote. The reason is returned per
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

        evidence_words = _words(answer.evidence_text or "")
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
