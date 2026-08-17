"""Phase 1 data-analysis substep: cluster the real corpus's free-text category
signal into review-ready groups, as empirical input for hand-authoring
core/taxonomy/data/taxonomy.v1.yaml.

`document_extractions.coverage_type`/`coverage_name` are free text (no enum),
with hundreds of near-duplicate phrasings across 8 companies (e.g. dozens of
differently-worded "loss of work capacity" values). Reviewing ~1800 distinct
values by hand isn't practical, so this script uses the existing local
embedding model (core/embeddings/model.py, zero marginal cost) to greedily
cluster near-duplicate values by cosine similarity, then writes a frequency-
ranked, human-reviewable JSON artifact. This script makes NO LLM calls and
writes NOTHING to the taxonomy config or the database - it only produces a
review artifact under data/processed/taxonomy_analysis/.

Usage: python scripts/analyze_coverage_taxonomy.py [--similarity-threshold 0.87]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from sqlalchemy import select  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import Document, DocumentExtraction  # noqa: E402
from core.database.session import session_scope  # noqa: E402
from core.embeddings.model import embed_texts  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.87


def _greedy_cluster(values: list[str], threshold: float) -> list[list[int]]:
    """Greedily group `values` by cosine similarity of their embeddings.

    "Leader" clustering (not a running centroid): each new value is compared
    against every existing cluster's ORIGINAL seed vector (the first value
    that started that cluster), never an averaged/drifting centroid. A
    running centroid was tried first and produced a single 673-member mega-
    cluster via chaining (A~B~C~D... even though A and D are unrelated) -
    comparing to a fixed seed avoids that drift. Order-dependent but fine
    for a human-reviewed exploratory artifact, not a final classifier.
    """
    if not values:
        return []
    vectors = np.array(embed_texts(values))
    clusters: list[list[int]] = []
    seeds: list[np.ndarray] = []
    for i, vector in enumerate(vectors):
        best_cluster = -1
        best_score = -1.0
        for c_idx, seed in enumerate(seeds):
            score = float(np.dot(vector, seed))
            if score > best_score:
                best_score = score
                best_cluster = c_idx
        if best_cluster != -1 and best_score >= threshold:
            clusters[best_cluster].append(i)
        else:
            clusters.append([i])
            seeds.append(vector)
    return clusters


def _cluster_frequency_table(
    counter: Counter[str], threshold: float, source_field: str
) -> list[dict]:
    distinct_values = list(counter)
    logger.info("Clustering %d distinct %s values...", len(distinct_values), source_field)
    clusters = _greedy_cluster(distinct_values, threshold)

    rows = []
    for cluster in clusters:
        members = [
            {"value": distinct_values[i], "frequency": counter[distinct_values[i]]}
            for i in cluster
        ]
        members.sort(key=lambda m: m["frequency"], reverse=True)
        rows.append(
            {
                "source_field": source_field,
                "total_frequency": sum(m["frequency"] for m in members),
                "member_count": len(members),
                "members": members,
            }
        )
    rows.sort(key=lambda r: r["total_frequency"], reverse=True)
    return rows


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--similarity-threshold", type=float, default=_DEFAULT_SIMILARITY_THRESHOLD)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)

    with session_scope() as session:
        coverage_types = Counter(
            v for v in session.scalars(select(DocumentExtraction.coverage_type)) if v and v.strip()
        )
        coverage_names = Counter(
            v for v in session.scalars(select(DocumentExtraction.coverage_name)) if v and v.strip()
        )
        department_names = Counter(
            v for v in session.scalars(select(Document.department_name)) if v and v.strip()
        )
        disease_lists = list(session.scalars(select(DocumentExtraction.disease_list)))

    disease_counter: Counter[str] = Counter()
    for disease_list in disease_lists:
        for disease in disease_list or []:
            if disease and disease.strip():
                disease_counter[disease.strip()] += 1

    logger.info(
        "Loaded %d distinct coverage_type, %d distinct coverage_name, "
        "%d distinct department_name, %d distinct disease_list values.",
        len(coverage_types),
        len(coverage_names),
        len(department_names),
        len(disease_counter),
    )

    coverage_type_clusters = _cluster_frequency_table(
        coverage_types, args.similarity_threshold, "coverage_type"
    )
    coverage_name_clusters = _cluster_frequency_table(
        coverage_names, args.similarity_threshold, "coverage_name"
    )

    out_dir = settings.processed_dir / "taxonomy_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "coverage_type_clusters.json").write_text(
        json.dumps(coverage_type_clusters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "coverage_name_clusters.json").write_text(
        json.dumps(coverage_name_clusters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "department_names.json").write_text(
        json.dumps(
            sorted(
                [{"value": k, "frequency": v} for k, v in department_names.items()],
                key=lambda r: r["frequency"],
                reverse=True,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "disease_list_frequencies.json").write_text(
        json.dumps(
            sorted(
                [{"value": k, "frequency": v} for k, v in disease_counter.items()],
                key=lambda r: r["frequency"],
                reverse=True,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Wrote %d coverage_type clusters, %d coverage_name clusters, "
        "%d department names, %d distinct diseases to %s",
        len(coverage_type_clusters),
        len(coverage_name_clusters),
        len(department_names),
        len(disease_counter),
        out_dir,
    )


if __name__ == "__main__":
    main()
