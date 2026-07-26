from __future__ import annotations

from core.matching.lexical import (
    combined_tokens,
    extract_tokens,
    group_scope_signature,
    has_lexical_overlap,
)


def test_extract_tokens_filters_generic_insurance_terms() -> None:
    assert extract_tokens("ביטוח חיים") == set()


def test_extract_tokens_filters_prefixed_generic_terms() -> None:
    # "לביטוח"/"וחיים" etc. are prefixed forms of generic terms, not content
    assert extract_tokens("לביטוח וחיים") == set()


def test_extract_tokens_keeps_content_words() -> None:
    assert extract_tokens("ניתוחים וטיפולים מחליפי ניתוח בישראל") == {
        "ניתוחים",
        "וטיפולים",
        "מחליפי",
        "ניתוח",
        "בישראל",
    }


def test_extract_tokens_drops_short_words() -> None:
    assert "של" not in extract_tokens("של הביטוח")


def test_extract_tokens_none_input() -> None:
    assert extract_tokens(None) == set()


def test_combined_tokens_unions_multiple_fields() -> None:
    result = combined_tokens("נספח 531", "ביטוח בריאות", "תרופות מיוחדות")
    assert "תרופות" in result
    assert "מיוחדות" in result


def test_has_lexical_overlap_exact_match() -> None:
    assert has_lexical_overlap({"ניתוחים", "בישראל"}, {"ניתוחים", "אחר"})


def test_has_lexical_overlap_suffix_containment() -> None:
    # "לתרופות" (prefixed content word) vs "תרופות" - not stripped, but
    # recovered via suffix containment.
    assert has_lexical_overlap({"לתרופות"}, {"תרופות"})


def test_has_lexical_overlap_short_tokens_dont_count_as_suffix_match() -> None:
    # "מגן" ends with "גן", but "גן" is only 2 chars - too short to count as
    # a meaningful suffix match (would otherwise false-positive on any two
    # unrelated words that happen to share a short 2-char ending).
    assert not has_lexical_overlap({"גן"}, {"מגן"})


def test_has_lexical_overlap_no_overlap() -> None:
    assert not has_lexical_overlap({"סיעודי", "הפניקס"}, {"אמבולטורי", "מגדל"})


def test_has_lexical_overlap_empty_bags() -> None:
    assert not has_lexical_overlap(set(), set())
    assert not has_lexical_overlap({"ניתוחים"}, set())


def test_real_degenerate_case_correctly_shows_no_overlap() -> None:
    """Regression test for a real corpus finding: two administrative
    documents (a Hachshara questionnaire and a Direct Insurance "required
    documents" page) whose only populated extraction field was an identical
    generic coverage_type ("ביטוח חיים") matched at a near-perfect embedding
    score (~1.000) despite having nothing to do with each other. Their
    appendix_name/coverage_type combined bags must show no overlap."""
    bag_a = combined_tokens("שאלון 1 - אשפוזים חיים ומשכנתא", "ביטוח חיים", None)
    bag_b = combined_tokens("פירוט מסמכים נדרשים", "ביטוח חיים", None)
    assert not has_lexical_overlap(bag_a, bag_b)


def test_has_lexical_overlap_prefix_containment() -> None:
    # "ניתוח" (singular) vs "ניתוחים"/"ניתוחיים" (plural forms) - the root is
    # a *prefix* of the inflected word, the opposite direction from the
    # preposition-glued suffix case above.
    assert has_lexical_overlap({"ניתוח"}, {"ניתוחים"})
    assert has_lexical_overlap({"ניתוח"}, {"ניתוחיים"})


def test_real_singular_plural_match_shows_overlap() -> None:
    """Regression test for a real corpus finding: a Migdal surgery-related
    appendix ("ניתוח", singular) and a Menorah one ("ניתוחיים"/"לניתוחים",
    plural forms) scored 96.98% but stayed pending_review because the
    existing suffix-only check doesn't catch a root word being a *prefix*
    of its plural form."""
    bag_a = combined_tokens("פיצוי לאשפוז עקב ניתוח", "ביטוח נוסף לאישפוז עקב ניתוח", "פיצוי לאישפוז עקב ניתוח")
    bag_b = combined_tokens("ביטוח ניתוחיים פלוס, נספח 125", "ביטוח רפואי לניתוחים", "ביטוח ניתוחים פלוס")
    assert has_lexical_overlap(bag_a, bag_b)


def test_group_scope_signature_none_when_no_marker() -> None:
    assert group_scope_signature("שלב", "אובדן כושר עבודה") is None
    assert group_scope_signature(None, None) is None


def test_group_scope_signature_extracts_employer_not_marker_word() -> None:
    # "בנק"/"עובדי"/"לעובדי" are markers, not part of the signature itself -
    # otherwise two different banks would spuriously "match" on "בנק".
    sig = group_scope_signature("הסכם אובדן כושר עבודה לעובדי בנק הפועלים", None)
    assert sig is not None
    assert "הפועלים" in sig
    assert "בנק" not in sig
    assert "עובדי" not in sig


def test_group_scope_signature_different_employers_dont_overlap() -> None:
    sig_a = group_scope_signature("עובדי בנק הפועלים", None)
    sig_b = group_scope_signature("עובדי בנק לאומי", None)
    assert not has_lexical_overlap(sig_a, sig_b)


def test_group_scope_signature_same_employer_different_phrasing_overlaps() -> None:
    sig_a = group_scope_signature("הסכם אובדן כושר עבודה לעובדי בנק הפועלים", None)
    sig_b = group_scope_signature('בנק הפועלים בע"מ (סכומי ביטוח מדורגים)', None)
    assert has_lexical_overlap(sig_a, sig_b)


def test_real_good_match_shows_overlap() -> None:
    """Regression test for a real corpus finding: a Menorah document and a
    Direct Insurance document both about surgery-alternative treatments in
    Israel, phrased differently but sharing distinctive content words."""
    bag_a = combined_tokens(
        "גילוי נאות - ניתוחים וטיפולים מחליפי ניתוח בישראל, נספח 736",
        "ניתוחים מהשקל הראשון",
        "ניתוחים וטיפולים מחליפי ניתוח בישראל",
    )
    bag_b = combined_tokens(
        "ניתוחים וטיפולים מחליפי ניתוח בישראל- גילוי נאות 99/04",
        "ניתוחים מהשקל הראשון",
        "פוליסה לביטוח ניתוחים וטיפולים מחליפי ניתוח בישראל",
    )
    assert has_lexical_overlap(bag_a, bag_b)
