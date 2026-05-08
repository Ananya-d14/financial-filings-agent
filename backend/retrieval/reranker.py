"""Local BGE-reranker-large cross-encoder.

Takes (query, [RetrievalResult, ...]) and re-scores each pair using
BAAI/bge-reranker-large. Returns the same list sorted by rerank_score desc,
truncated to top_k.

Why a cross-encoder rather than a bi-encoder for reranking?
Cross-encoders jointly encode the query and each candidate, producing a
relevance score that's far more accurate than dot-product similarity between
separately-encoded vectors, at the cost of O(n) model calls instead of one.
Practical budget: 20 candidates × ~200ms each = ~4s on RTX 2080 Ti.

Singleton pattern: the model is loaded once per process and reused. This
avoids the ~3-5s load time on every request.
"""

from __future__ import annotations

from typing import Any

from backend.config import get_settings
from backend.logging_config import get_logger

log = get_logger(__name__)

_reranker: Any = None  # sentence_transformers.CrossEncoder


def get_reranker() -> Any:
    """Load and cache the BGE-reranker-large cross-encoder."""
    global _reranker
    if _reranker is not None:
        return _reranker

    from sentence_transformers import CrossEncoder
    import torch

    settings = get_settings()
    device_setting = settings.embedding_device
    device = "cuda" if (device_setting == "auto" and torch.cuda.is_available()) else (
        device_setting if device_setting != "auto" else "cpu"
    )

    log.info("reranker.loading", model=settings.reranker_model, device=device)
    _reranker = CrossEncoder(settings.reranker_model, device=device)
    log.info("reranker.loaded")
    return _reranker


def rerank_results(
    query: str,
    results: list[Any],  # list[RetrievalResult]. typed as Any to avoid circular import
    top_k: int = 10,
) -> list[Any]:
    """Re-score retrieval results with the cross-encoder; return top_k sorted."""
    if not results:
        return results

    reranker = get_reranker()

    pairs = [(query, r.text) for r in results]
    scores = reranker.predict(pairs)

    for result, score in zip(results, scores):
        result.rerank_score = float(score)

    results.sort(key=lambda r: r.rerank_score or 0.0, reverse=True)

    log.debug("reranker.done", input=len(results), output=min(top_k, len(results)))
    return results[:top_k]
