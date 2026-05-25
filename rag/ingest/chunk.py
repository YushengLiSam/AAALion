"""Split product JSON into retrievable chunks.

Each product yields multiple chunks:
- 1 chunk for marketing_description
- 1 chunk per official_faq entry  (q + a)
- 1 chunk per user_reviews entry  (rating + content)

Every chunk carries the product_id, category, brand, and base_price as
metadata so retrieval can filter on them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


@dataclass
class Chunk:
    product_id: str
    chunk_type: str  # "desc" | "faq" | "review"
    text: str
    metadata: dict = field(default_factory=dict)


def _meta(product: dict) -> dict:
    provenance = product.get("provenance") or {}
    return {
        "product_id": product.get("product_id"),
        "category": product.get("category"),
        "sub_category": product.get("sub_category"),
        "brand": product.get("brand"),
        "base_price": product.get("base_price"),
        "currency": provenance.get("currency", "CNY"),
    }


def chunks_from_product(product: dict) -> Iterator[Chunk]:
    pid = product.get("product_id")
    if not isinstance(pid, str):
        return
    meta = _meta(product)
    rag = product.get("rag_knowledge", {}) or {}

    desc = rag.get("marketing_description")
    if isinstance(desc, str) and desc.strip():
        yield Chunk(product_id=pid, chunk_type="desc", text=desc.strip(), metadata=meta)

    for faq in rag.get("official_faq", []) or []:
        q = faq.get("question", "")
        a = faq.get("answer", "")
        if q or a:
            yield Chunk(
                product_id=pid,
                chunk_type="faq",
                text=f"问：{q}\n答：{a}",
                metadata=meta,
            )

    for review in rag.get("user_reviews", []) or []:
        rating = review.get("rating")
        content = review.get("content", "")
        if not content:
            continue
        yield Chunk(
            product_id=pid,
            chunk_type="review",
            text=f"评分 {rating}/5：{content}",
            metadata={**meta, "rating": rating},
        )


def iter_products(seed_root: Path) -> Iterator[dict]:
    for path in seed_root.glob("*/data/*.json"):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


def all_chunks(seed_root: Path) -> Iterable[Chunk]:
    for product in iter_products(seed_root):
        yield from chunks_from_product(product)
