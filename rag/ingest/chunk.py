"""Split product JSON into retrievable chunks.

Each product yields multiple chunks:
- 1 chunk for marketing_description
- 1 chunk per official_faq entry  (q + a)
- 1 chunk per user_reviews entry  (rating + content)
- 1 chunk summarising the SKU options (颜色 / 尺码 / 容量 …)        [RAG_INDEX_SKU, default on]
- 1 chunk with the offline image caption (tools/caption_images.py) [RAG_INDEX_IMAGE_CAPTIONS, default on;
  only when data/derived/image_captions.jsonl has a row for the product]

Every chunk carries the product_id, category, brand, and base_price as
metadata so retrieval can filter on them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTIONS = REPO_ROOT / "data" / "derived" / "image_captions.jsonl"


@dataclass
class Chunk:
    product_id: str
    chunk_type: str  # "desc" | "faq" | "review" | "sku" | "image_caption"
    text: str
    metadata: dict = field(default_factory=dict)


def _brand_country(product: dict) -> str:
    """商品的品牌国别(ISO 两位码),未知时为空串。

    必须和反选逻辑用同一个解析函数 ``brand_origin.product_origin``:
    它对 AI 生成的演示商品会改用品牌查表,而不是直接信 ``provenance.origin_country``
    (那个字段对演示数据一律默认填 "CN",欧莱雅也是 CN)。入库和查询共用一个
    事实来源,过滤下推到数据库后才不会和 Python 侧的反选结果不一致。
    """
    try:
        from rag.retrieve.brand_origin import product_origin

        return (product_origin(product) or "").upper()
    except Exception:
        return ""


def _meta(product: dict) -> dict:
    provenance = product.get("provenance") or {}
    return {
        "product_id": product.get("product_id"),
        "category": product.get("category"),
        "sub_category": product.get("sub_category"),
        "brand": product.get("brand"),
        "brand_country": _brand_country(product),
        "base_price": product.get("base_price"),
        "currency": provenance.get("currency", "CNY"),
    }


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=4)
def _load_captions(path: str) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("product_id") and isinstance(row.get("caption"), dict):
            out[row["product_id"]] = row["caption"]
    return out


def image_caption_text(product_id: str) -> str | None:
    """离线图片描述(做法 A)拼成一段可检索的中文。没有描述就返回 None。

    只含看得见的外观:形态、颜色(仅这张图里这一款)、材质、风格、图中文字。
    品牌 / 价格 / 功效以商品 JSON 为准,不从描述里取。"""
    caps = _load_captions(os.getenv("RAG_IMAGE_CAPTIONS_PATH") or str(DEFAULT_CAPTIONS))
    c = caps.get(product_id)
    if not c:
        return None
    parts = []
    if c.get("appearance"):
        parts.append(f"外观:{c['appearance']}")
    # visible_text 默认不进索引(RAG_CAPTION_INCLUDE_TEXT=1 才进):抽检发现 2B 模型会编图里没有的字
    # (帐篷图写出 "Tent" 和一串 "1000",插线板写出 "CUBO" "USB"),而品牌 / 型号本来就在商品 JSON 里,
    # 这一项新增信息少、幻觉风险高。数据文件里照样保留,方便复核。
    keys = [("colors", "颜色"), ("materials", "材质"), ("style", "风格")]
    if _flag("RAG_CAPTION_INCLUDE_TEXT", "0"):
        keys.append(("visible_text", "图中文字"))
    for key, label in keys:
        vals = [v for v in c.get(key) or [] if v]
        if vals:
            parts.append(f"{label}:{'、'.join(vals)}")
    return ";".join(parts) or None


def sku_text(product: dict) -> str | None:
    """把 SKU 规格汇总成一段文字:"可选规格:颜色:黑色、白色;尺码:S码、M码"。

    颜色 / 尺码等是结构化事实(25 个商品带颜色),比看图猜颜色可靠;每个商品只有一张图,
    图里只能看到一种颜色。"""
    skus = product.get("skus") or []
    values: dict[str, list[str]] = {}
    for s in skus:
        for k, v in (s.get("properties") or {}).items():
            v = str(v).strip()
            if k and v and v not in values.setdefault(k, []):
                values[k].append(v)
    if not values:
        return None
    body = ";".join(f"{k}:{'、'.join(vs[:12])}" for k, vs in values.items())
    return f"可选规格:{body}"


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

    if _flag("RAG_INDEX_SKU"):
        st = sku_text(product)
        if st:
            yield Chunk(product_id=pid, chunk_type="sku", text=st, metadata=meta)

    if _flag("RAG_INDEX_IMAGE_CAPTIONS"):
        ct = image_caption_text(pid)
        if ct:
            yield Chunk(product_id=pid, chunk_type="image_caption", text=ct, metadata=meta)


def iter_products(seed_root: Path) -> Iterator[dict]:
    for path in seed_root.glob("*/data/*.json"):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


def all_chunks(seed_root: Path) -> Iterable[Chunk]:
    for product in iter_products(seed_root):
        yield from chunks_from_product(product)
