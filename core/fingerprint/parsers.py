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
_PERIOD_UNIT_TO_DAYS = {
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


def parse_period_to_days(text: str | None) -> int | None:
    """Extracts the FIRST number+unit period mention and converts to days.

    Real policy text often lists several periods for different sub-cases
    in one string (e.g. "90 יום למקרה ביטוח ראשון; 365 ימים למקרה ביטוח
    נוסף; 5 שנים ממועד ההחלמה המלאה מסרטן") - the first one is
    consistently the general/base case in the corpus sampled, so it's used
    as the pragmatic default. This is a v1 simplification, not a full
    parse of every conditional sub-period; multi-condition periods remain
    fully available as free text via *_period_raw alongside this value.
    """
    if not text:
        return None
    match = _PERIOD_PATTERN.search(text)
    if not match:
        return None
    number = int(match.group(1))
    unit = match.group(2)
    if unit in ("שעה", "שעות"):
        return max(1, round(number / 24))
    return number * _PERIOD_UNIT_TO_DAYS[unit]


# "יחידות סכום ביטוח" (insurance-amount UNITS) is Migdal's indexed-unit
# system - the shekel value of one unit lives elsewhere in the policy, not
# in this string, so a bare "600 יחידות..." must NOT be parsed as ₪600.
_UNIT_INDICATOR = re.compile(r"יחיד")

_ILS_PATTERN = re.compile(r"(?:₪\s*([\d,]+(?:\.\d+)?))|(?:([\d,]+(?:\.\d+)?)\s*(?:₪|ש[\"'״]?ח))")
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
