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
"""

from __future__ import annotations

import re

_CONNECTOR = r"[\s'\"׳-]*(?:מס['׳]?)?[\s'\"׳-]*"
_APPENDIX_MENTION = re.compile(
    rf"(?:נספח|נטפח)(?:ים)?{_CONNECTOR}(\d+(?:\s*(?:,|ו-?|-)\s*\d+)*)"
)
_DIGITS = re.compile(r"\d+")


def find_appendix_numbers(text: str) -> list[str]:
    """Return every appendix number mentioned in `text`.

    Order is first-seen, deduplicated — a number repeated across pages
    (common: the same appendix number footers every page) counts once.
    """
    numbers: list[str] = []
    for mention in _APPENDIX_MENTION.finditer(text):
        for number in _DIGITS.findall(mention.group(1)):
            if number not in numbers:
                numbers.append(number)
    return numbers
