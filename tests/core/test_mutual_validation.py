from __future__ import annotations

from core.matching.mutual_validation import compute_group_validation, compute_mutual_matches


def test_mutual_match_true_when_reverse_edge_exists() -> None:
    pairs = {("a", "b"), ("b", "a")}
    result = compute_mutual_matches(pairs)
    assert result[("a", "b")] is True
    assert result[("b", "a")] is True


def test_mutual_match_false_when_one_sided() -> None:
    pairs = {("a", "b")}
    result = compute_mutual_matches(pairs)
    assert result[("a", "b")] is False


def test_group_validation_consistent_triangle() -> None:
    mutual = {("a", "b"), ("b", "a"), ("a", "c"), ("c", "a")}
    all_pairs = mutual | {("b", "c")}
    result = compute_group_validation(mutual, all_pairs)
    assert result[("a", "b")] == "CONSISTENT"
    assert result[("a", "c")] == "CONSISTENT"


def test_group_validation_flags_unclosed_triangle() -> None:
    """A<->B and A<->C are mutual, but B and C were never matched at all -
    the real-world case req 20/21 warns about: matches aren't automatically
    transitive."""
    mutual = {("a", "b"), ("b", "a"), ("a", "c"), ("c", "a")}
    all_pairs = mutual  # note: no (b, c) or (c, b) edge anywhere
    result = compute_group_validation(mutual, all_pairs)
    assert result[("a", "b")] == "INCONSISTENT"
    assert result[("a", "c")] == "INCONSISTENT"


def test_group_validation_single_mutual_pair_has_no_triangle_to_check() -> None:
    mutual = {("a", "b"), ("b", "a")}
    result = compute_group_validation(mutual, mutual)
    assert result[("a", "b")] == "CONSISTENT"


def test_group_validation_low_confidence_edge_still_counts_as_closing() -> None:
    """The triangle-closing edge doesn't need to be mutual itself - any
    recorded match (even one-directional, even low-confidence pending_review)
    between B and C is enough to not flag the triangle as broken."""
    mutual = {("a", "b"), ("b", "a"), ("a", "c"), ("c", "a")}
    all_pairs = mutual | {("c", "b")}  # one-directional is enough
    result = compute_group_validation(mutual, all_pairs)
    assert result[("a", "b")] == "CONSISTENT"
    assert result[("a", "c")] == "CONSISTENT"
