"""Lexical corroboration for cross-company matches.

Embedding similarity alone can be fooled: a document with almost nothing
extracted (an administrative form/questionnaire the LLM found no coverage
content in) reduces `PolicyExtraction.embedding_text()` to a short generic
string like "ביטוח חיים" - and then ANY other equally-empty document, from
any company, "matches" it at a near-perfect score with no real relationship
between them (confirmed live: 59 of 65 matches scoring >=0.999 across the
whole DB turned out to be exactly this pattern). This module provides a
cheap, additional sanity check: do the two documents' appendix_name/
coverage_type/coverage_name actually share a meaningful word?

Deliberately NOT doing blind prefix-stripping on arbitrary words: an early
version stripped a leading "ב" off any word to handle Hebrew's habit of
gluing prepositions directly onto the next word (e.g. "בבית" = "in the
house") - but this corrupted "ביטוח" ("insurance") into "יטוח", which then
spuriously overlapped between two otherwise-unrelated documents (confirmed
live - it recreated the exact false-positive this module exists to catch).
Instead, only a small, closed vocabulary (generic insurance boilerplate +
stopwords) is expanded with its own prefixed variants; unknown content words
are left untouched. This is safe (never corrupts a real word) at the cost of
occasionally missing a prefixed variant of a content word (e.g. "תרופות" vs
"לתרופות") - `has_lexical_overlap`'s suffix-containment check recovers most
of that.
"""

from __future__ import annotations

import re

_STOPWORDS = {
    "של", "עם", "על", "אל", "מן", "כל", "לא", "או", "גם", "כי", "אם",
    "זה", "זו", "אלו", "עד", "בין", "אין", "יש", "את", "לפי", "כפי",
}
# Boilerplate that appears in almost every insurance document regardless of
# what it actually covers - sharing one of these carries no signal about
# whether two documents describe the same coverage.
_GENERIC_INSURANCE_TERMS = {
    "ביטוח", "חיים", "בריאות", "פוליסה", "נספח", "גילוי", "נאות", "תנאים",
    "כלליים", "מקרה", "כיסוי", "כתב", "שירות", "תכנית", "מוצר", "מהדורה",
    "עדכון", "מעודכן", "נוסח", "פרטי", "כללי", "נוסף", "מחלות",
}
_PREFIX_LETTERS = ("ו", "ב", "כ", "ל", "מ", "ש", "ה")

_WORD_RE = re.compile(r"[א-ת]+")
_MIN_TOKEN_LENGTH = 2
_MIN_SUFFIX_MATCH_LENGTH = 3


def _expand_with_prefixes(base_words: set[str]) -> set[str]:
    """Add every 1- and 2-letter Hebrew-prefix variant of each word in `base_words`.

    Only ever applied to this module's own small, known vocabulary (stopwords
    + generic insurance terms) - never to arbitrary content tokens, which is
    what makes this safe (see module docstring).
    """
    expanded = set(base_words)
    for word in base_words:
        for first in _PREFIX_LETTERS:
            expanded.add(first + word)
            for second in _PREFIX_LETTERS:
                expanded.add(first + second + word)
    return expanded


_EXCLUDED = _expand_with_prefixes(_STOPWORDS | _GENERIC_INSURANCE_TERMS)


def extract_tokens(text: str | None) -> set[str]:
    """Extract meaningful Hebrew word tokens from `text`.

    Filters out stopwords, generic insurance boilerplate (and their common
    prefixed forms), and tokens shorter than 2 characters.
    """
    if not text:
        return set()
    return {
        word
        for word in _WORD_RE.findall(text)
        if len(word) >= _MIN_TOKEN_LENGTH and word not in _EXCLUDED
    }


def combined_tokens(*texts: str | None) -> set[str]:
    """Union of `extract_tokens` over several fields (e.g. appendix_name,
    coverage_type, coverage_name) - one combined bag-of-words per document."""
    tokens: set[str] = set()
    for text in texts:
        tokens |= extract_tokens(text)
    return tokens


def has_lexical_overlap(bag_a: set[str], bag_b: set[str]) -> bool:
    """Do these two token bags share a meaningful word?

    Exact match, or one token being a >=3-char affix of the other:

    - suffix containment recovers cases like "תרופות" vs "לתרופות" - a
      preposition glued onto the front of an otherwise-identical word.
    - prefix containment recovers the opposite, more common direction: a
      plural/grammatical suffix glued onto the end of a shared root, e.g.
      "ניתוח" vs "ניתוחים"/"ניתוחיים" (confirmed live - a real Migdal/Menorah
      surgery-coverage match stayed stuck in pending_review because "ניתוח"
      is a prefix, not a suffix, of "ניתוחים").

    Neither direction ever strips/mutates a token; see module docstring for
    why blind stripping is unsafe here.
    """
    if bag_a & bag_b:
        return True
    for token_a in bag_a:
        if len(token_a) < _MIN_SUFFIX_MATCH_LENGTH:
            continue
        for token_b in bag_b:
            if len(token_b) < _MIN_SUFFIX_MATCH_LENGTH:
                continue
            if (
                token_a.endswith(token_b)
                or token_b.endswith(token_a)
                or token_a.startswith(token_b)
                or token_b.startswith(token_a)
            ):
                return True
    return False
