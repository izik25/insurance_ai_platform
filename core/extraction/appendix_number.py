"""Generic appendix-number extraction, shared across every company module.

Looks for the Hebrew pattern "נספח <number>" (singular) or "נספחים <n1>,
<n2>, ..." (plural — a single document can legitimately cover more than
one appendix number, e.g. "נספחים 101, 102").
"""

from __future__ import annotations

import re

_APPENDIX_MENTION = re.compile(r"נספח(?:ים)?\s+(\d+(?:\s*(?:,|ו-?|-)\s*\d+)*)")
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
