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
    sub_category: str | None = None
    sub_categories: list[str] | None = None
    brand_include: list[str] | None = None
    brand_exclude: list[str] | None = None
    # R8: country-keyword exclusions ("日系" / "美系" / "韩系" ...) extracted
    # locally at turn time and carried across multi-turn conversations.
    # Consumed by `apply_negation` via `brand_origin.excluded_countries()`.
    # Stored here (not just as a per-turn `apply_negation` call) so that a
    # turn like "再便宜点的呢" inherits "不要日系" from a prior turn.
    exclude_keywords: list[str] | None = None
    price_max_cny: float | None = None
    price_min_cny: float | None = None
    # Kept for callers created before CNY semantics were made explicit.
    price_max: float | None = None
    price_min: float | None = None

    @property
    def effective_price_max_cny(self) -> float | None:
        return self.price_max_cny if self.price_max_cny is not None else self.price_max

    @property
    def effective_price_min_cny(self) -> float | None:
        return self.price_min_cny if self.price_min_cny is not None else self.price_min

    @property
    def has_price_constraint(self) -> bool:
        return self.effective_price_min_cny is not None or self.effective_price_max_cny is not None

    @property
    def active(self) -> bool:
        return any(
            (
                self.category,
                self.sub_category,
                self.sub_categories,
                self.brand_include,
                self.brand_exclude,
                self.exclude_keywords,
                self.has_price_constraint,
            )
        )


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
    sub_categories = f.sub_categories or ([f.sub_category] if f.sub_category else None)
    if sub_categories:
        parts.append({"sub_category": {"$in": sub_categories}})
    if f.brand_include:
        parts.append({"brand": {"$in": f.brand_include}})
    if f.brand_exclude:
        parts.append({"brand": {"$nin": f.brand_exclude}})
    if f.has_price_constraint:
        cny_price_parts: list[dict] = [{"currency": "CNY"}]
        if f.effective_price_min_cny is not None:
            cny_price_parts.append({"base_price": {"$gte": f.effective_price_min_cny}})
        if f.effective_price_max_cny is not None:
            cny_price_parts.append({"base_price": {"$lte": f.effective_price_max_cny}})
        # Foreign-source amounts cannot be compared with a RMB budget until
        # response-time FX normalization. Keep them in the candidate pool.
        parts.append(
            {
                "$or": [
                    {"$and": cny_price_parts},
                    {"currency": {"$ne": "CNY"}},
                ]
            }
        )
    if not parts:
        return None
    return {"$and": parts} if len(parts) > 1 else parts[0]


def product_matches_filter(product: dict, f: Filter | None, *, strict_cny_price: bool = False) -> bool:
    """Apply product-level filtering shared by dense/BM25 and final results.

    During retrieval, foreign-source products pass RMB price bounds because
    their live CNY value is not indexed. After currency normalization,
    ``strict_cny_price=True`` enforces that budget with ``price_cny``.
    """
    if f is None:
        return True
    if f.category and product.get("category") != f.category:
        return False
    sub_categories = f.sub_categories or ([f.sub_category] if f.sub_category else None)
    if sub_categories and product.get("sub_category") not in sub_categories:
        return False

    brand = str(product.get("brand", "")).casefold()
    if f.brand_include and brand not in {item.casefold() for item in f.brand_include}:
        return False
    if f.brand_exclude and brand in {item.casefold() for item in f.brand_exclude}:
        return False

    if not f.has_price_constraint:
        return True
    provenance = product.get("provenance") or {}
    currency = str(provenance.get("currency", "CNY")).upper()
    if currency != "CNY" and not strict_cny_price:
        return True
    raw_price = product.get("price_cny") if currency != "CNY" else product.get("price_cny", product.get("base_price"))
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return False
    if f.effective_price_min_cny is not None and price < f.effective_price_min_cny:
        return False
    if f.effective_price_max_cny is not None and price > f.effective_price_max_cny:
        return False
    return True


def apply_product_filter(products: Iterable[dict], f: Filter | None, *, strict_cny_price: bool = False) -> list[dict]:
    return [product for product in products if product_matches_filter(product, f, strict_cny_price=strict_cny_price)]


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
        if not product_matches_filter(products[pid], f):
            continue
        if pid not in seen or raw_hit.score > seen[pid].score:
            seen[pid] = Hit(product_id=pid, score=raw_hit.score, product=products[pid])
    return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:k]


def query_image(image_bytes: bytes, k: int = 5) -> list[Hit]:
    """Top-k visually similar products. Embeds the input image with OpenCLIP
    and queries the `products_image` Chroma collection."""
    try:
        from rag.ingest.embed_image import embed_image_bytes
        from rag.store import query_image as store_query_image
    except ImportError:
        return []
    try:
        vec = embed_image_bytes(image_bytes)
        raw = store_query_image(vec, k=k)
    except Exception as e:
        import sys
        print(f"[rag] query_image failed: {e}", file=sys.stderr)
        return []

    products = _product_index()
    hits: list[Hit] = []
    for raw_hit in raw:
        pid = (raw_hit.metadata or {}).get("product_id") or raw_hit.id
        if pid in products:
            hits.append(Hit(product_id=pid, score=raw_hit.score, product=products[pid]))
    return hits


def _keyword_fallback(text: str, k: int = 5, f: Filter | None = None) -> list[Hit]:
    products = apply_product_filter(_product_index().values(), f)
    if not text.strip():
        return [Hit(p["product_id"], 0.0, p) for p in products[:k]]
    scored = []
    for p in products:
        s = sum(1 for ch in text if ch in p.get("title", ""))
        s += sum(0.5 for ch in text if ch in (p.get("rag_knowledge", {}) or {}).get("marketing_description", ""))
        scored.append(Hit(p["product_id"], s, p))
    scored.sort(key=lambda h: h.score, reverse=True)
    return scored[:k]
