"""Top-k retrieval over the text index. Uses the Chroma store via
``rag.store``. Returns one product dict per hit (de-duplicated by
product_id and ordered by best chunk score).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Filter:
    category: str | None = None
    brand_exclude: list[str] | None = None
    price_max: float | None = None
    price_min: float | None = None


@dataclass
class Hit:
    product_id: str
    score: float
    product: dict


@lru_cache(maxsize=1)
def _product_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pid = p.get("product_id")
        if isinstance(pid, str):
            out[pid] = p
    return out


def _build_where(f: Filter | None) -> dict | None:
    if f is None:
        return None
    parts: list[dict] = []
    if f.category:
        parts.append({"category": f.category})
    if f.price_min is not None:
        parts.append({"base_price": {"$gte": f.price_min}})
    if f.price_max is not None:
        parts.append({"base_price": {"$lte": f.price_max}})
    if not parts:
        return None
    return {"$and": parts} if len(parts) > 1 else parts[0]


def query(text: str, k: int = 5, f: Filter | None = None) -> list[Hit]:
    try:
        from rag.ingest.embed_text import embed_query
        from rag.store import query_text
    except ImportError:
        return _keyword_fallback(text, k=k, f=f)

    try:
        vec = embed_query(text or " ")
        raw = query_text(vec, k=k * 3, where=_build_where(f))
    except Exception:
        return _keyword_fallback(text, k=k, f=f)

    products = _product_index()
    seen: dict[str, Hit] = {}
    for raw_hit in raw:
        pid = raw_hit.metadata.get("product_id") if raw_hit.metadata else None
        if not pid or pid not in products:
            continue
        if f and f.brand_exclude and products[pid].get("brand") in f.brand_exclude:
            continue
        if pid not in seen or raw_hit.score > seen[pid].score:
            seen[pid] = Hit(product_id=pid, score=raw_hit.score, product=products[pid])
    return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:k]


def _keyword_fallback(text: str, k: int = 5, f: Filter | None = None) -> list[Hit]:
    products = list(_product_index().values())
    if f:
        if f.category:
            products = [p for p in products if p.get("category") == f.category]
        if f.brand_exclude:
            products = [p for p in products if p.get("brand") not in f.brand_exclude]
        if f.price_min is not None:
            products = [p for p in products if (p.get("base_price") or 0) >= f.price_min]
        if f.price_max is not None:
            products = [p for p in products if (p.get("base_price") or 0) <= f.price_max]
    if not text.strip():
        return [Hit(p["product_id"], 0.0, p) for p in products[:k]]
    scored = []
    for p in products:
        s = sum(1 for ch in text if ch in p.get("title", ""))
        s += sum(0.5 for ch in text if ch in (p.get("rag_knowledge", {}) or {}).get("marketing_description", ""))
        scored.append(Hit(p["product_id"], s, p))
    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:k]
