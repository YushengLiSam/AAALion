"""混合检索:对稠密检索(Chroma)与 BM25 的结果做倒数排名融合(Reciprocal Rank Fusion, RRF)。

为什么选 RRF:它不依赖稠密/稀疏两路检索器各自的绝对分数量纲——只看排名(rank),
天然规避了两路分数不可比的问题。调参常数 k=60 沿用 RRF 原始论文
(Cormack et al. 2009)中的标准取值。
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
    """用 RRF 融合两个已排序的结果列表。

    每个 `*_hits` 形如 `[(product_id, product_dict), ...]`,按各自得分降序排列。
    返回按融合后的 RRF 分数排序的前 top_k 个结果。
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


def hybrid_topk(text: str, k: int = 10, dense_k: int = 20, bm25_k: int = 20, f=None) -> list[HybridHit]:
    """便捷封装:依次执行稠密检索 + BM25 检索,再做 RRF 融合。"""
    from rag.retrieve.query import query
    from rag.retrieve.bm25 import bm25_topk

    dense = query(text, k=dense_k, f=f)
    bm25 = bm25_topk(text, k=bm25_k, f=f)

    dense_pairs = [(h.product_id, h.product) for h in dense]
    bm25_pairs = [(pid, prod) for pid, _, prod in bm25]
    return reciprocal_rank_fusion(dense_pairs, bm25_pairs, top_k=k)
