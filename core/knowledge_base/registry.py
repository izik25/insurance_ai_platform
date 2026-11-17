"""Loaders for the question bank / canonical codes / concepts config files.

`normalize_to_code()` is deliberately rule-based-only (substring match
against each code's canonical name + synonyms) - it is the cheap first pass
described in the matching-upgrade plan; callers should treat a `None`
return as "send this phrase to the LLM fallback", not as "no code exists".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from core.exceptions import ConfigurationError
from core.knowledge_base.schema import (
    CanonicalCode,
    CanonicalCodesConfig,
    ConceptsConfig,
    QuestionBankConfig,
    QuestionDef,
)

_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_VERSION = "v1"


def _load_yaml(filename: str) -> dict:
    path = _DATA_DIR / filename
    if not path.exists():
        raise ConfigurationError(f"Knowledge base config not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache
def get_question_bank(version: str = DEFAULT_VERSION) -> QuestionBankConfig:
    return QuestionBankConfig.model_validate(_load_yaml(f"question_bank.{version}.yaml"))


def get_questions_for_category(
    category_id: str | None, version: str = DEFAULT_VERSION
) -> list[QuestionDef]:
    """Base questions + this category's own questions, base first."""
    bank = get_question_bank(version)
    category_specific = bank.category_questions.get(category_id, []) if category_id else []
    return [*bank.base_questions, *category_specific]


@lru_cache
def get_canonical_codes_config(version: str = DEFAULT_VERSION) -> CanonicalCodesConfig:
    return CanonicalCodesConfig.model_validate(_load_yaml(f"canonical_codes.{version}.yaml"))


def get_canonical_codes(
    code_category: str | None = None, version: str = DEFAULT_VERSION
) -> list[CanonicalCode]:
    codes = get_canonical_codes_config(version).codes
    if code_category is None:
        return codes
    return [c for c in codes if c.code_category == code_category]


@lru_cache
def _normalization_index(
    code_category: str | None, version: str = DEFAULT_VERSION
) -> tuple[tuple[str, str], ...]:
    """(lowercased phrase, code) pairs - canonical names and synonyms alike -
    sorted longest-phrase-first so a specific synonym wins over a shorter,
    more generic one that happens to be a substring of it."""
    pairs: list[tuple[str, str]] = []
    for code in get_canonical_codes(code_category, version):
        for phrase in [code.canonical_name_he, *code.synonyms]:
            if phrase and phrase.strip():
                pairs.append((phrase.strip(), code.code))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return tuple(pairs)


def normalize_to_code(
    phrase: str, code_category: str | None = None, version: str = DEFAULT_VERSION
) -> str | None:
    """Rule-based synonym/substring lookup against the canonical codes dictionary.

    Returns None when nothing matches - callers should fall back to an
    LLM-assisted normalization pass for that phrase, not assume "no code".
    """
    normalized = phrase.strip()
    if not normalized:
        return None
    for known_phrase, code in _normalization_index(code_category, version):
        if known_phrase in normalized or normalized in known_phrase:
            return code
    return None


def codes_mentioned(
    phrase: str, code_category: str, version: str = DEFAULT_VERSION
) -> list[str]:
    """Every code whose canonical name or synonym appears in `phrase`, in
    order of first mention."""
    text = phrase.strip()
    positions: dict[str, int] = {}
    for known_phrase, code in _normalization_index(code_category, version):
        position = text.find(known_phrase)
        if position != -1 and position < positions.get(code, len(text) + 1):
            positions[code] = position
    return sorted(positions, key=positions.__getitem__)


def normalize_to_earliest_code(
    phrase: str, code_category: str, version: str = DEFAULT_VERSION
) -> str | None:
    """Like normalize_to_code, but when several codes' phrases appear in the
    text, returns the one mentioned FIRST rather than the longest phrase.

    For single-valued fields described in a sentence that also mentions a
    secondary element - e.g. benefit_type "פיצוי חודשי ... וכן החזר הוצאות
    שיקום" (a real long-term-care appendix) - the leading phrase is the main
    one; longest-phrase-wins picked REIMBURSEMENT there.
    """
    text = phrase.strip()
    if not text:
        return None
    best: tuple[int, int, str] | None = None
    for known_phrase, code in _normalization_index(code_category, version):
        position = text.find(known_phrase)
        if position == -1:
            continue
        candidate = (position, -len(known_phrase), code)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else None


@lru_cache
def get_concepts(version: str = DEFAULT_VERSION) -> ConceptsConfig:
    return ConceptsConfig.model_validate(_load_yaml(f"concepts.{version}.yaml"))
