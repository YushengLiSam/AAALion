"""文本索引上的 Top-k 检索。通过 ``rag.store`` 访问 Chroma 存储。
每个命中返回一个商品 dict(按 product_id 去重,并按该商品最佳
chunk 得分排序)。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]

# P0.4 —— 进程级兜底计数。稠密检索出错时 query() 会悄悄退回关键词检索,用户照样有结果,
# 所以"向量库挂了"在日志之外完全看不出来。这里累计次数并记下最近一次的错误,
# 由 /ready 展示(以后 /metrics 也从这里取)。只增不减,进程重启归零。
_fallback_lock = threading.Lock()
_fallback_counts: dict[str, int] = {"dense_to_keyword": 0, "image_query_failed": 0}
_fallback_last: dict[str, object] = {"kind": None, "error": None, "at": None}


def _count_fallback(kind: str, exc: BaseException | None) -> None:
    with _fallback_lock:
        _fallback_counts[kind] = _fallback_counts.get(kind, 0) + 1
        _fallback_last["kind"] = kind
        _fallback_last["error"] = f"{type(exc).__name__}: {exc}"[:300] if exc is not None else None
        _fallback_last["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")


def fallback_stats() -> dict:
    """稠密→关键词兜底等降级的累计次数(本进程),供 /ready 展示。"""
    with _fallback_lock:
        return {**_fallback_counts, "last": dict(_fallback_last)}


@dataclass
class Filter:
    category: str | None = None
    sub_category: str | None = None
    sub_categories: list[str] | None = None
    brand_include: list[str] | None = None
    brand_exclude: list[str] | None = None
    # R8: 国别关键词排除("日系" / "美系" / "韩系" ...),在每轮对话时本地抽取,
    # 并在多轮对话间持续生效。
    # 由 `apply_negation` 经 `brand_origin.excluded_countries()` 消费。
    # 之所以存在这里(而不是只做每轮一次的 `apply_negation` 调用),是为了让
    # "再便宜点的呢" 这样的后续轮次能继承前一轮的 "不要日系" 约束。
    exclude_keywords: list[str] | None = None
    price_max_cny: float | None = None
    price_min_cny: float | None = None
    # 为兼容在 CNY 语义显式化之前写的调用方而保留。
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
        # 外币商品的金额在响应阶段做汇率(FX)归一化之前,无法与人民币预算直接比较,
        # 因此先保留在候选池中。
        parts.append(
            {
                "$or": [
                    {"$and": cny_price_parts},
                    {"currency": {"$ne": "CNY"}},
                ]
            }
        )
    # R15 —— 国别反选("不要日系")下推到数据库。只有当前索引确实带
    # brand_country 字段(schema v2 重建过)时才下推;旧索引会自动跳过,
    # 由 product_matches_filter 里的 Python 侧同一条规则兜底。
    countries = _pushdown_countries(f)
    if countries:
        parts.append({"brand_country": {"$nin": sorted(countries)}})
    if not parts:
        return None
    return {"$and": parts} if len(parts) > 1 else parts[0]


def _filter_pushdown_on() -> bool:
    return os.getenv("RAG_FILTER_PUSHDOWN", "1") == "1"


@lru_cache(maxsize=256)
def _excluded_countries_cached(keywords: tuple[str, ...]) -> frozenset[str]:
    try:
        from rag.retrieve.brand_origin import excluded_countries

        return frozenset(excluded_countries(list(keywords)))
    except Exception:
        return frozenset()


def _filter_excluded_countries(f: Filter | None) -> frozenset[str]:
    if f is None or not f.exclude_keywords or not _filter_pushdown_on():
        return frozenset()
    return _excluded_countries_cached(tuple(k for k in f.exclude_keywords if k))


@lru_cache(maxsize=1)
def _current_origin_fp() -> str:
    from rag.retrieve.brand_origin import origin_fingerprint

    return origin_fingerprint(_product_index().values())


def _pushdown_countries(f: Filter | None) -> frozenset[str]:
    """能下推到数据库的被排除国别。三个条件缺一不可:开关打开、索引带 brand_country、
    索引里 brand_country 是用**当前**产地解析结果算的(指纹一致)。任一不满足就只走
    Python 侧的同一条规则——结果不变,只是少了数据库层的提前过滤。"""
    countries = _filter_excluded_countries(f)
    if not countries:
        return frozenset()
    try:
        from rag.store import get_store

        store = get_store()
        if "brand_country" not in store.filterable_fields():
            return frozenset()
        if store.index_properties().get("origin_fp") != _current_origin_fp():
            return frozenset()
    except Exception:
        return frozenset()
    return countries


def product_matches_filter(product: dict, f: Filter | None, *, strict_cny_price: bool = False) -> bool:
    """商品级过滤,供稠密检索/BM25 与最终结果共用。

    检索阶段,外币商品会直接通过人民币价格区间约束——因为其实时 CNY 价格
    并未入索引。完成货币归一化后,用 ``strict_cny_price=True`` 基于
    ``price_cny`` 严格执行预算约束。
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

    # R15 —— 国别反选的 Python 侧版本,与数据库下推是同一条规则、同一个解析函数
    # (brand_origin.product_origin),和后面 apply_negation 的国别判断也一致。
    # 放在这里是为了让稠密检索与 BM25 两路在截 top-k 之前就剔除被排除国别,
    # 候选名额不再浪费在注定会被删掉的商品上。开关:RAG_FILTER_PUSHDOWN。
    excluded = _filter_excluded_countries(f)
    if excluded:
        try:
            from rag.retrieve.brand_origin import product_origin

            origin = product_origin(product)
        except Exception:
            origin = None
        if origin and origin in excluded:
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
    except ImportError as exc:
        _count_fallback("dense_to_keyword", exc)
        return _keyword_fallback(text, k=k, f=f)

    try:
        vec = embed_query(text or " ")
        raw = query_text(vec, k=k * 3, where=_build_where(f))
    except Exception as exc:
        # 向量库出错时退回关键词检索,保证可用;但必须留痕——否则换后端后
        # 即使 Milvus 整个挂掉,评测也可能靠关键词兜底"看起来还行"。
        print(f"[rag] dense query failed, falling back to keyword search: {type(exc).__name__}: {exc}", file=sys.stderr)
        _count_fallback("dense_to_keyword", exc)
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
    """返回视觉上最相似的 Top-k 商品。用 OpenCLIP 对输入图片做向量化,
    再查询 `products_image` 这个 Chroma collection。"""
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
        _count_fallback("image_query_failed", e)
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
