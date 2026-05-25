"""BM25 over the product catalog. Tokenizes with jieba so Chinese text
splits into meaningful units rather than per-character.

Corpus is built lazily from product JSON files in data/seed/*/data/*.json.
Each document is the concatenation of title + brand + sub_category +
marketing_description, indexed by product_id.
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
    # jieba handles Chinese; drop punctuation + whitespace afterward
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
    """Return [(product_id, score, product_dict), …] sorted by BM25 score."""
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
