"""Hybrid retrieval: Reciprocal Rank Fusion of dense (Chroma) + BM25.

Why RRF: it doesn't depend on the absolute score scales of dense vs sparse
retrievers — only on rank. Standard tuning constant k=60 from the original
RRF paper (Cormack et al. 2009).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HybridHit:
    product_id: str
    rrf_score: float
    dense_rank: int | None
    bm25_rank: int | None
    product: dict


def reciprocal_rank_fusion(
    dense_hits: list[tuple[str, dict]],
    bm25_hits: list[tuple[str, dict]],
    *,
    k: int = 60,
    top_k: int = 10,
) -> list[HybridHit]:
    """Fuse two ranked lists by RRF.

    Each `*_hits` is `[(product_id, product_dict), ...]` in score order.
    Returns top_k hits ordered by combined RRF score.
    """
    scores: dict[str, float] = {}
    dense_rank: dict[str, int] = {pid: i for i, (pid, _) in enumerate(dense_hits)}
    bm25_rank: dict[str, int] = {pid: i for i, (pid, _) in enumerate(bm25_hits)}
    products: dict[str, dict] = {}

    for i, (pid, p) in enumerate(dense_hits):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + i + 1)
        products[pid] = p
    for i, (pid, p) in enumerate(bm25_hits):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + i + 1)
        products.setdefault(pid, p)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [
        HybridHit(
            product_id=pid,
            rrf_score=score,
            dense_rank=dense_rank.get(pid),
            bm25_rank=bm25_rank.get(pid),
            product=products[pid],
        )
        for pid, score in ranked
    ]


def hybrid_topk(text: str, k: int = 10, dense_k: int = 20, bm25_k: int = 20) -> list[HybridHit]:
    """Convenience: run dense + BM25 + fuse."""
    from rag.retrieve.query import query
    from rag.retrieve.bm25 import bm25_topk

    dense = query(text, k=dense_k)
    bm25 = bm25_topk(text, k=bm25_k)

    dense_pairs = [(h.product_id, h.product) for h in dense]
    bm25_pairs = [(pid, prod) for pid, _, prod in bm25]
    return reciprocal_rank_fusion(dense_pairs, bm25_pairs, top_k=k)
