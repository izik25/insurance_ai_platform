"""Candidate Index: category -> company -> [document_id], built once per
matching run from already-classified documents. Narrows a document's
candidate search to its own (or an explicitly declared alternative)
category before any hard-constraint or scoring work happens - this is what
makes "a new document only touches its own category slice, never the whole
corpus" true in practice, not just an aspiration.

Built in-memory - no persisted index table. At this corpus's size (low
thousands) this is the same "trivially fast, no ANN index needed" judgment
call core/matching/similarity.py's own module docstring already makes for
its dense cosine-similarity matrix.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateIndexEntry:
    document_id: str
    company_id: str
    category_id: str
    is_active: bool


class CandidateIndex:
    def __init__(self, entries: list[CandidateIndexEntry]) -> None:
        self._by_category: dict[str, list[CandidateIndexEntry]] = defaultdict(list)
        self._by_id: dict[str, CandidateIndexEntry] = {}
        for entry in entries:
            self._by_category[entry.category_id].append(entry)
            self._by_id[entry.document_id] = entry

    def candidates_for(self, document_id: str) -> list[CandidateIndexEntry]:
        """Other-company, ACTIVE (Document.is_active) entries in the source
        document's own category or any of its alternative_categories - the
        source document itself never needs to be active (see
        core/matching/similarity.py's DocumentMeta.is_active docstring for
        the same asymmetric principle: a superseded document can still be a
        match SOURCE, just never a candidate)."""
        source = self._by_id.get(document_id)
        if source is None:
            return []
        seen: dict[str, CandidateIndexEntry] = {}
        for entry in self._by_category.get(source.category_id, []):
            if entry.company_id == source.company_id or not entry.is_active:
                continue
            seen[entry.document_id] = entry
        return list(seen.values())

    def candidates_for_categories(
        self, document_id: str, category_ids: list[str]
    ) -> list[CandidateIndexEntry]:
        """Same as candidates_for, but also pulls in every category_id
        passed explicitly (e.g. the source's alternative_categories) -
        separated from candidates_for so a caller that doesn't care about
        alternative categories (e.g. a unit test) gets the simpler method."""
        source = self._by_id.get(document_id)
        if source is None:
            return []
        seen: dict[str, CandidateIndexEntry] = {}
        for category_id in {source.category_id, *category_ids}:
            for entry in self._by_category.get(category_id, []):
                if entry.company_id == source.company_id or not entry.is_active:
                    continue
                seen[entry.document_id] = entry
        return list(seen.values())
