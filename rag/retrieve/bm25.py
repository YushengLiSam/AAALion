"""基于商品目录的 BM25 检索。使用 jieba 分词,让中文文本切分成
有意义的词语单元,而不是逐字切分。

语料库从 data/seed/*/data/*.json 中的商品 JSON 文件惰性构建。
每篇文档由 title + brand + sub_category + marketing_description
拼接而成,以 product_id 作为索引键。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tokenize(text: str) -> list[str]:
    import jieba
    # jieba 负责中文分词;分词后再过滤掉标点和空白符
    raw = jieba.lcut(text or "")
    return [t for t in raw if re.search(r"[\w一-鿿]", t)]


def _iter_products() -> Iterable[dict]:
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


def _document_text(p: dict) -> str:
    rag = p.get("rag_knowledge", {}) or {}
    parts = [
        p.get("title", ""),
        p.get("brand", ""),
        p.get("category", ""),
        p.get("sub_category", ""),
        (rag.get("marketing_description") or "")[:400],
    ]
    return " ".join([s for s in parts if s])


@lru_cache(maxsize=1)
def _index():
    from rank_bm25 import BM25Okapi
    products: list[dict] = list(_iter_products())
    ids: list[str] = [p["product_id"] for p in products]
    docs: list[list[str]] = [_tokenize(_document_text(p)) for p in products]
    bm25 = BM25Okapi(docs)
    by_id: dict[str, dict] = {p["product_id"]: p for p in products}
    return bm25, ids, by_id


def bm25_topk(query: str, k: int = 10, f=None) -> list[tuple[str, float, dict]]:
    """返回按 BM25 分数降序排列的 [(product_id, score, product_dict), …] 列表。"""
    if not query.strip():
        return []
    bm25, ids, by_id = _index()
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    scores = bm25.get_scores(q_tokens)
    if f is not None:
        from rag.retrieve.query import product_matches_filter

        scored = [(pid, score) for pid, score in zip(ids, scores) if product_matches_filter(by_id[pid], f)]
    else:
        scored = list(zip(ids, scores))
    ranked = sorted(scored, key=lambda x: x[1], reverse=True)[:k]
    return [(pid, float(s), by_id[pid]) for pid, s in ranked if s > 0]
