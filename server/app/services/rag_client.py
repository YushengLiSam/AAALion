"""Backend's view of the RAG layer. Combines hybrid retrieval (dense + BM25),
optional query rewriting, negation filtering, and cross-encoder reranking.

The default path is:
  user_text → (optional) rewrite → hybrid retrieve top-20 → apply negation
            → rerank → apply price intent → top-k products.

Toggle via env vars:
  RAG_REWRITE=1   enable LLM query expansion (default off — costs API calls)
  RAG_NEGATION=1  enable LLM negation extraction (auto-on when 不要/除了/不含 in query)
  RAG_RERANK=1    enable cross-encoder rerank (default on)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _negation_signals(text: str) -> bool:
    return any(s in text for s in ("不要", "不含", "不带", "除了", "排除", "no ", "without"))


def top_k(text: str, k: int = 5) -> list[dict]:
    """Hybrid-retrieve + (optional) rewrite + negation-filter + rerank → top-k products."""
    rewrite_on = os.getenv("RAG_REWRITE", "0") == "1"
    rerank_on = os.getenv("RAG_RERANK", "1") == "1"
    negation_on = (os.getenv("RAG_NEGATION", "1") == "1") and _negation_signals(text)
    price_on = os.getenv("RAG_PRICE_INTENT", "1") == "1"

    # 1) Optional rewrite to multi-query.
    queries: list[str] = [text]
    if rewrite_on:
        try:
            from rag.retrieve.rewrite import rewrite_query
            queries = rewrite_query(text) or [text]
        except Exception:
            queries = [text]

    # 2) Hybrid retrieve top-20 across all queries, dedupe by product_id.
    try:
        from rag.retrieve.hybrid import hybrid_topk
        seen: dict[str, dict] = {}
        for q in queries:
            for h in hybrid_topk(q, k=20):
                if h.product_id not in seen:
                    seen[h.product_id] = h.product
        candidates = list(seen.values())
    except Exception:
        try:
            from rag.retrieve.query import query
            candidates = [h.product for h in query(text, k=20)]
        except Exception:
            from rag.retrieve.query import _keyword_fallback  # type: ignore
            return [h.product for h in _keyword_fallback(text, k=k)]

    # 3) Negation filter (drops violating candidates).
    if negation_on:
        try:
            from rag.retrieve.negation import extract_negation, apply_negation
            neg = extract_negation(text)
            candidates = apply_negation(candidates, neg)
        except Exception:
            pass

    # 4) Rerank with cross-encoder. Keep a slightly larger pool when price
    # intent may reorder candidates into the final top-k.
    rerank_limit = max(k, 10) if price_on else k
    if rerank_on and len(candidates) > k:
        try:
            from rag.retrieve.rerank import rerank
            candidates = rerank(text, candidates, top_k=rerank_limit)
        except Exception:
            candidates = candidates[:rerank_limit]
    else:
        candidates = candidates[:rerank_limit]

    # 5) Price intent is a final preference layer, after semantic relevance.
    if price_on:
        try:
            from app.services.price_intent import apply_price_intent
            candidates = apply_price_intent(candidates, text)
        except Exception:
            pass

    return candidates[:k]


def top_k_image(image_bytes: bytes, k: int = 3) -> list[dict]:
    """Visually similar top-k via CLIP. Empty list if CLIP isn't available."""
    try:
        from rag.retrieve.query import query_image
        hits = query_image(image_bytes, k=k)
        return [h.product for h in hits]
    except Exception:
        return []


stub_top_k = top_k
