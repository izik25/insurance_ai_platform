"""Local multilingual embedding model.

A local model (not a paid API) so embedding thousands of documents costs
nothing beyond compute time. multilingual-e5 has solid Hebrew coverage,
unlike most English-only sentence-transformer models.
"""

from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from core.config.settings import get_settings


@lru_cache
def get_embedding_model() -> SentenceTransformer:
    """Return the process-wide embedding model singleton (loaded once)."""
    return SentenceTransformer(get_settings().embedding_model_name)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector (as a plain list) per text.

    E5 models are instruction-tuned: for symmetric similarity (comparing
    documents to other documents, not a query to a passage), the model card
    recommends prefixing every input with "query: " for best results.
    """
    model = get_embedding_model()
    prefixed = [f"query: {text}" for text in texts]
    vectors = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
    return [vector.tolist() for vector in vectors]
