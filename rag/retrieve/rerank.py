"""Cross-encoder reranker.

Takes top-N candidates from hybrid retrieval, scores each with a Chinese-
aware cross-encoder, returns top-k by reranker score. Cross-encoders are
slower than dual-encoders but much more accurate for the final ranking pass.

Model: BAAI/bge-reranker-base (~280 MB, CPU-friendly).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Sequence


_DEFAULT_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(_DEFAULT_MODEL, max_length=256)


def warmup_reranker() -> None:
    """Load the cross-encoder and run one tiny prediction before traffic."""
    model = _model()
    model.predict(
        [("推荐一款日常洁面产品", "温和洁面乳 适合日常清洁")],
        batch_size=1,
        show_progress_bar=False,
    )


def rerank(query: str, candidates: Sequence[dict], top_k: int = 5) -> list[dict]:
    """Rerank product dict candidates by cross-encoder score against the query.
    Returns the top_k. On any failure (model not installed, etc.), falls
    back to input order."""
    if not query or len(candidates) <= 1:
        return list(candidates)[:top_k]
    try:
        model = _model()
    except Exception as e:
        import sys
        print(f"[rerank] cross-encoder unavailable: {e}", file=sys.stderr)
        return list(candidates)[:top_k]

    pairs: list[tuple[str, str]] = []
    for p in candidates:
        rag = p.get("rag_knowledge", {}) or {}
        doc = " ".join([
            p.get("title", ""),
            p.get("brand", ""),
            (rag.get("marketing_description") or "")[:240],
        ])
        pairs.append((query, doc))

    scores = model.predict(pairs, batch_size=8, show_progress_bar=False)
    ranked = sorted(zip(list(candidates), scores), key=lambda x: float(x[1]), reverse=True)
    return [p for p, _ in ranked[:top_k]]
