"""Generic appendix-number extraction, shared across every company module.

Looks for the Hebrew pattern "נספח <number>" (singular) or "נספחים <n1>,
<n2>, ..." (plural — a single document can legitimately cover more than
one appendix number, e.g. "נספחים 101, 102").

Two tolerances on top of the plain "נספח <number>" shape, both confirmed
live against real Migdal scans that were falling through to a useless
filename-hint fallback (a 6+ digit internal media ID, not a real appendix
number) because this regex was too strict to match what was actually on
the page:

- "נטפח" alongside "נספח" - OCR misreads the ס as a ט in low-quality
  scans (confirmed: a real document's OCR output read "נטפח 970-971" for
  a printed "נספח 970-971").
- An optional "מס'"/"מס׳"/"מס" ("no.") and/or stray punctuation between
  the word and the digits (confirmed: "נספח מס'-801-806/91").

A third, structurally different shape (added 2026-08-13, confirmed live on
~2011-2014-era Migdal documents): the number list comes *before* the
keyword, parenthesized - e.g. "(1598 ,1597 ,1596 ,1595 ,1594) נספח ניתוחים
בחו"ל" - one document covering plan numbers 1594-1598. Matched separately
from the "after" shape above since the numbers, not the keyword, anchor
the match here.
"""

from __future__ import annotations

import re

_CONNECTOR = r"[\s'\"׳-]*(?:מס['׳]?)?[\s'\"׳-]*"
_APPENDIX_MENTION = re.compile(
    rf"(?:נספח|נטפח)(?:ים)?{_CONNECTOR}(\d+(?:\s*(?:,|ו-?|-)\s*\d+)*)"
)
_APPENDIX_MENTION_PARENTHESIZED = re.compile(
    r"\((\d+(?:\s*,\s*\d+)*)\)\s*(?:נספח|נטפח|תוכנית)(?:ים)?"
)
_DIGITS = re.compile(r"\d+")


# One number in a source-provided list, with an optional standalone Hebrew
# version letter ("805א'", "805 (א')", "805('א)") - the lookahead keeps a
# following word ("923 נספח") from being swallowed as a suffix.
_LIST_TOKEN = re.compile(r"\d+(?:\s*\(?\s*['׳]?[א-ת]['׳]?\s*\)?(?![א-ת]))?")
_LIST_SEPARATORS = re.compile(r"[\s,;]*")


def split_appendix_number_list(values: list[str]) -> list[str]:
    """Split values that hold several appendix numbers in one string.

    Harel's archive puts a booklet's whole rider list in its single
    "appendix number" cell (e.g. "804,931,851,929,805א', 904,945,946,947"),
    and the LLM backfill does the same with printed plan lists - stored as
    one element, none of those numbers was findable by exact lookup
    (confirmed 2026-10-13: 66 documents). A value is split only when
    nothing but commas/semicolons/whitespace sits between its numbers;
    anything with "/" or "-" ("45/02", "970-971") is a format of its own
    and kept as-is. All-zero tokens ("1,000,000" read as a list) are dropped.
    """
    result: list[str] = []
    for value in values:
        parts = [value]
        if "/" not in value and "-" not in value:
            tokens = _LIST_TOKEN.findall(value)
            if len(tokens) > 1 and _LIST_SEPARATORS.fullmatch(_LIST_TOKEN.sub("", value)):
                parts = [re.sub(r"\s+", "", t) for t in tokens if t.strip("0")]
        for part in parts:
            if part and part not in result:
                result.append(part)
    return result


def find_appendix_numbers(text: str) -> list[str]:
    """Return every appendix number mentioned in `text`.

    Order is first-seen, deduplicated — a number repeated across pages
    (common: the same appendix number footers every page) counts once.
    """
    numbers: list[str] = []
    for pattern in (_APPENDIX_MENTION, _APPENDIX_MENTION_PARENTHESIZED):
        for mention in pattern.finditer(text):
            for number in _DIGITS.findall(mention.group(1)):
                if number not in numbers:
                    numbers.append(number)
    return numbers
