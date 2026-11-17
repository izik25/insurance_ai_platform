"""Rule-based Hebrew parsers for periods, monetary amounts, and percentages -
pure code, no LLM, no network. This is the "Rule-Based Validation... periods
... amounts" requirement (req 22): turning free text like "90 יום מתחילת
הביטוח" or "₪500,000" into a trustworthy number belongs here, not to an LLM
guess made while building the Canonical Coverage Profile.

Deliberately conservative: every function returns None rather than guessing
when the text doesn't match a recognized pattern. A missing normalized
value must never be silently invented (same principle as the FOUND/
NOT_FOUND/NOT_APPLICABLE/AMBIGUOUS question-bank statuses).

Patterns are calibrated against real strings pulled from the Phase 2 pilot
corpus (data/processed/taxonomy_analysis/period_amount_samples.txt), not
hand-guessed - see e.g. multi-condition periods ("90 יום למקרה ביטוח
ראשון; 365 ימים למקרה ביטוח נוסף"), week/hour units ("6 שבועות", "96
שעות"), and Migdal's "יחידות סכום ביטוח" (insurance-amount *units*, not a
directly convertible currency figure - deliberately excluded, see
parse_amount).
"""

from __future__ import annotations

import re

# Order matters: a plural/longer form that a shorter form is a PREFIX of
# must come first in the alternation, since regex alternation picks the
# first alternative that matches at a given position, not the longest
# (e.g. "חודש" is a prefix of "חודשים" - if "חודש" came first, matching
# "5 חודשים" would only ever capture "חודש").
PERIOD_UNIT_TO_DAYS = {
    "שנתיים": 730,
    "שנים": 365,
    "שנה": 365,
    "שבועות": 7,
    "שבוע": 7,
    "חודשים": 30,
    "חודש": 30,
    "ימים": 1,
    "יום": 1,
}
_PERIOD_PATTERN = re.compile(
    r"(\d+)\s*(שנתיים|שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום|שעות|שעה)"
)

# Spelled-out numbers ("שלושה חודשים", "שנים עשר חודשים") - confirmed live:
# Menorah appendix 108's waiting period is written "שלושה חודשים" and came
# out of the v1 parser as no number at all. Compound forms first, for the
# same prefix reason as PERIOD_UNIT_TO_DAYS above ("שנים עשר" vs "שנים").
HEBREW_NUMBER_WORDS = {
    "שנים עשר": 12, "שניים עשר": 12, "שתים עשרה": 12, "שתיים עשרה": 12,
    "אחד עשר": 11, "אחת עשרה": 11,
    "אחד": 1, "אחת": 1, "שניים": 2, "שתיים": 2, "שני": 2, "שתי": 2,
    "שלושה": 3, "שלוש": 3, "ארבעה": 4, "ארבע": 4, "חמישה": 5, "חמש": 5,
    "שישה": 6, "שש": 6, "שבעה": 7, "שבע": 7, "שמונה": 8, "תשעה": 9, "תשע": 9,
    "עשרה": 10, "עשר": 10, "עשרים": 20, "שלושים": 30, "שישים": 60, "תשעים": 90,
}
_WORD_PERIOD_PATTERN = re.compile(
    r"(?<![א-ת])("
    + "|".join(sorted((re.escape(w) for w in HEBREW_NUMBER_WORDS), key=len, reverse=True))
    + r")\s+(שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום|שעות|שעה)"
)
# Dual forms are a number+unit in one word ("חודשיים" = two months).
_DUAL_PERIOD_PATTERN = re.compile(r"(?<![א-ת])(שנתיים|חודשיים|שבועיים|יומיים)(?![א-ת])")
DUAL_TO_DAYS = {"שנתיים": 730, "חודשיים": 60, "שבועיים": 14, "יומיים": 2}
# "<unit> [one word] <number>" - unit (optionally with ה-), at most one word,
# then the number; the number must not itself start a new number+unit.
_REVERSED_PERIOD_PATTERN = re.compile(
    r"(?<![א-ת])ה?(שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום)(?:\s+[א-ת]+)?\s+(\d+)"
    r"(?!\d)(?!\s*(?:שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום|שעות|שעה|%|₪))"
)


def parse_period_to_days(text: str | None) -> int | None:
    """Extracts the FIRST number+unit period mention and converts to days.

    Real policy text often lists several periods for different sub-cases
    in one string (e.g. "90 יום למקרה ביטוח ראשון; 365 ימים למקרה ביטוח
    נוסף; 5 שנים ממועד ההחלמה המלאה מסרטן") - the first one is
    consistently the general/base case in the corpus sampled, so it's used
    as the pragmatic default. This is a v1 simplification, not a full
    parse of every conditional sub-period; multi-condition periods remain
    fully available as free text via *_period_raw alongside this value.

    Digits, spelled-out numbers ("שלושה חודשים") and dual forms
    ("חודשיים") are all recognized; whichever appears first in the text
    wins. A zero/OCR-damaged number ("00 יום" - a real scanned Harel
    appendix) returns None rather than a 0-day period.
    """
    if not text:
        return None
    candidates: list[tuple[int, int | None]] = []

    match = _PERIOD_PATTERN.search(text)
    if match:
        number = int(match.group(1))
        unit = match.group(2)
        if number == 0:
            days = None
        elif unit in ("שעה", "שעות"):
            days = max(1, round(number / 24))
        else:
            days = number * PERIOD_UNIT_TO_DAYS[unit]
        candidates.append((match.start(), days))

    match = _WORD_PERIOD_PATTERN.search(text)
    if match:
        number = HEBREW_NUMBER_WORDS[match.group(1)]
        unit = match.group(2)
        if unit in ("שעה", "שעות"):
            days = max(1, round(number / 24))
        else:
            days = number * PERIOD_UNIT_TO_DAYS[unit]
        candidates.append((match.start(), days))

    match = _DUAL_PERIOD_PATTERN.search(text)
    if match:
        candidates.append((match.start(), DUAL_TO_DAYS[match.group(1)]))

    if not candidates:
        # RTL reading order can put the number after its unit (Phoenix 5630:
        # "הימים הראשונים 90 תקופת האכשרה..." for "90 הימים הראשונים").
        # Only a fallback - the normal order always wins when present.
        match = _REVERSED_PERIOD_PATTERN.search(text)
        if match and 0 < int(match.group(2)) < 1000:
            return int(match.group(2)) * PERIOD_UNIT_TO_DAYS[match.group(1)]
        return None
    return min(candidates, key=lambda c: c[0])[1]


# "יחידות סכום ביטוח" (insurance-amount UNITS) is Migdal's indexed-unit
# system - the shekel value of one unit lives elsewhere in the policy, not
# in this string, so a bare "600 יחידות..." must NOT be parsed as ₪600.
_UNIT_INDICATOR = re.compile(r"יחיד")

_ILS_PATTERN = re.compile(
    r"(?:₪\s*([\d,]+(?:\.\d+)?))|(?:([\d,]+(?:\.\d+)?)\s*(?:₪|ש[\"'״]?ח|שקלים|שקל|ש\.ח))"
)
_USD_PATTERN = re.compile(r"(?:\$\s*([\d,]+(?:\.\d+)?))|(?:([\d,]+(?:\.\d+)?)\s*(?:\$|דולר))")

_PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def parse_amount(text: str | None) -> tuple[float, str] | None:
    """Returns (value, currency) for the first recognized ILS/USD amount in
    `text`, or None if nothing parses (including deliberately for
    unit-based amounts - see _UNIT_INDICATOR)."""
    if not text:
        return None
    if _UNIT_INDICATOR.search(text):
        return None

    match = _ILS_PATTERN.search(text)
    if match:
        raw = match.group(1) or match.group(2)
        try:
            return float(raw.replace(",", "")), "ILS"
        except ValueError:
            return None

    match = _USD_PATTERN.search(text)
    if match:
        raw = match.group(1) or match.group(2)
        try:
            return float(raw.replace(",", "")), "USD"
        except ValueError:
            return None

    return None


def parse_percentage(text: str | None) -> float | None:
    if not text:
        return None
    match = _PERCENT_PATTERN.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


_ANY_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NUMBER_WITH_UNIT = re.compile(r"(\d[\d,]*)\s*(שנתיים|שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום)")
_WORD_WITH_UNIT = re.compile(
    r"(?<![א-ת])("
    + "|".join(sorted((re.escape(w) for w in HEBREW_NUMBER_WORDS), key=len, reverse=True))
    + r")\s+(שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום)"
)


def normalize_number(raw: str) -> str:
    value = raw.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


def numbers_mentioned(text: str) -> set[str]:
    """Every number mentioned in `text`, plus the day-counts its periods
    convert to (so "3 חודשים" also supports "90 יום") and the values of
    spelled-out numbers ("שלושה חודשים") - normalized via normalize_number.
    Used to check one text's numbers against another's."""
    numbers = {normalize_number(m) for m in _ANY_NUMBER.findall(text)}
    for raw, unit in _NUMBER_WITH_UNIT.findall(text):
        value = int(raw.replace(",", ""))
        numbers.add(str(value * PERIOD_UNIT_TO_DAYS.get(unit, 1)))
    for word, unit in _WORD_WITH_UNIT.findall(text):
        value = HEBREW_NUMBER_WORDS[word]
        numbers.add(str(value))
        numbers.add(str(value * PERIOD_UNIT_TO_DAYS.get(unit, 1)))
    for dual, days in DUAL_TO_DAYS.items():
        if dual in text:
            numbers.add(str(days))
    return numbers
