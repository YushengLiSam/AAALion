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

    # R11.fix — preload CLIP (the image→image retriever) too. It loads a
    # ~600 MB OpenCLIP model lazily on the first 拍照找货 (~37 s cold), so a
    # demo-day restart would make the first photo query look hung. Warm it by
    # running one real image query against a seed product image.
    clip = "disabled"
    if os.getenv("RAG_PREWARM_CLIP", "1") == "1":
        try:
            import glob

            from rag.retrieve.query import query_image

            imgs = glob.glob(str(REPO_ROOT / "data" / "seed" / "**" / "images" / "*.jpg"), recursive=True)
            if imgs:
                with open(imgs[0], "rb") as f:
                    query_image(f.read(), k=1)
                clip = "ready"
        except Exception:
            clip = "error"

    return {
        "prewarm": "completed",
        "embedding": "ready",
        "bm25": "ready",
        "reranker": reranker,
        "clip": clip,
        "query_path": "ready",
    }
