"""Top-k retrieval over the text + image Qdrant collections.

Real implementation will use ``qdrant_client``. For now this is a
keyword-overlap fallback so the rest of the system can be developed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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


def _load_products() -> list[dict]:
    out: list[dict] = []
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _apply_filter(products: Iterable[dict], f: Filter | None) -> list[dict]:
    if f is None:
        return list(products)
    out = []
    for p in products:
        if f.category and p.get("category") != f.category:
            continue
        if f.brand_exclude and p.get("brand") in f.brand_exclude:
            continue
        price = p.get("base_price")
        if isinstance(price, (int, float)):
            if f.price_max is not None and price > f.price_max:
                continue
            if f.price_min is not None and price < f.price_min:
                continue
        out.append(p)
    return out


def query(text: str, k: int = 5, f: Filter | None = None) -> list[Hit]:
    """Keyword-overlap fallback. TODO(tujie): swap for Qdrant search."""
    products = _apply_filter(_load_products(), f)
    if not text.strip():
        return [Hit(p["product_id"], 0.0, p) for p in products[:k]]
    hits = []
    for p in products:
        score = sum(1 for ch in text if ch in p.get("title", ""))
        score += sum(
            0.5 for ch in text
            if ch in p.get("rag_knowledge", {}).get("marketing_description", "")
        )
        hits.append(Hit(p["product_id"], score, p))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
