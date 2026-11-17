"""Mutual/Group Validation over a computed match set - pure functions over
plain (document_id, matched_document_id) pairs, no DB, no LLM.

Mutual: A->B is reinforced if B->A also exists (both sides independently
picked each other as their best candidate for the other's company) - req 19.

Group: matches are NOT assumed transitive (req 20/21). If A and B are a
mutual pair, and A and C are also a mutual pair, but B and C were never
matched to each other at all, that's a real inconsistency worth surfacing,
not something to silently ignore.
"""

from __future__ import annotations

from collections import defaultdict


def compute_mutual_matches(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], bool]:
    """`pairs` is the full set of (document_id, matched_document_id) edges
    in the computed match set. Returns, for every edge, whether the reverse
    edge also exists."""
    return {(a, b): (b, a) in pairs for (a, b) in pairs}


def compute_group_validation(
    mutual_pairs: set[tuple[str, str]], all_pairs: set[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """For every document A with two or more mutual partners (B, C, ...),
    flags the A-B and A-C edges as "INCONSISTENT" whenever B and C were
    never matched to each other in either direction - an un-closed
    mutual-match triangle. Edges not involved in any such triangle are
    "CONSISTENT". Only 1-hop triangles are checked (A's direct mutual
    partners against each other), not arbitrary-depth transitivity chains -
    a deliberate scope limit, not an oversight: deeper chains multiply
    false-positive risk faster than they add real signal at this stage.
    """
    partners_by_source: dict[str, set[str]] = defaultdict(set)
    for a, b in mutual_pairs:
        partners_by_source[a].add(b)

    status: dict[tuple[str, str], str] = {pair: "CONSISTENT" for pair in mutual_pairs}
    for source, partners in partners_by_source.items():
        partner_list = sorted(partners)
        for i in range(len(partner_list)):
            for j in range(i + 1, len(partner_list)):
                b, c = partner_list[i], partner_list[j]
                if (b, c) not in all_pairs and (c, b) not in all_pairs:
                    status[(source, b)] = "INCONSISTENT"
                    status[(source, c)] = "INCONSISTENT"
    return status
