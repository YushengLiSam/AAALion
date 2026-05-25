"""Warm the lazy retrieval models before the backend accepts chat requests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def warm_retrieval_pipeline() -> dict[str, str]:
    """Load retrieval-time models and caches synchronously during startup."""
    if os.getenv("RAG_PREWARM", "1") != "1":
        return {"prewarm": "disabled", "embedding": "lazy", "bm25": "lazy", "reranker": "lazy"}

    from rag.ingest.embed_text import embed_query
    from rag.retrieve.bm25 import bm25_topk

    sample_query = "推荐一款日常洁面产品"
    embed_query(sample_query)
    bm25_topk(sample_query, k=1)

    reranker = "disabled"
    if os.getenv("RAG_RERANK", "1") == "1":
        from rag.retrieve.rerank import warmup_reranker

        warmup_reranker()
        reranker = "ready"

    # Exercise Chroma, hybrid fusion and a realistic candidate rerank before
    # readiness, so the first user request does not initialize that path.
    from app.services.rag_client import top_k

    top_k("推荐适合日常使用的商品", k=5)

    return {
        "prewarm": "completed",
        "embedding": "ready",
        "bm25": "ready",
        "reranker": reranker,
        "query_path": "ready",
    }
