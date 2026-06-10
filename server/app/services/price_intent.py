"""轻量级的价格意图(price-intent)解析与候选商品排序。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter
from typing import Literal, Sequence


PriceDirection = Literal["cheap", "expensive"]


@dataclass(frozen=True)
class PriceIntent:
    direction: PriceDirection | None = None
    price_min: float | None = None
    price_max: float | None = None

    @property
    def active(self) -> bool:
        return self.direction is not None or self.price_min is not None or self.price_max is not None


# R13 — 同时覆盖英文的方向/上下限表述, 这样 "any cheaper ones?" 会按价格升序排,
# 而不是沿用 reranker 的原始顺序(它往往把贵的排在前面)。
_CHEAP_RE = re.compile(
    r"便宜|平价|性价比|低价|划算|预算有限|预算友好"
    r"|\bcheap(?:er|est)?\b|\bafford|\bbudget[- ]?friendly|\bless expensive|\blower price",
    re.IGNORECASE,
)
_EXPENSIVE_RE = re.compile(
    r"贵|高端|高价|旗舰|预算充足|好一点"
    r"|\bexpensive\b|\bpricier\b|\bpremium\b|\bhigh[- ]?end\b|\bflagship\b",
    re.IGNORECASE,
)
_PRICE_MAX_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以内|以下|内|封顶|不超过|不要超过)")
# R13 — 处理标记词在前的说法("不超过300"); 上面那条"数字在前"的正则
# 永远匹配不到这种写法(它的 不超过 分支要求标记词出现在数字之后)。
_PRICE_MAX_PREFIX_RE = re.compile(r"(?:不要?超过|最多|顶多)\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?")
_PRICE_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以上|起|起步)")
_PRICE_MAX_EN_RE = re.compile(
    r"(?:under|below|less than|within|up to|no more than|cheaper than|max\.?|≤|<=?)\s*"
    r"[¥￥$]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PRICE_MIN_EN_RE = re.compile(
    r"(?:over|above|more than|at least|starting (?:at|from)|≥|>=?)\s*[¥￥$]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def parse_price_intent(text: str) -> PriceIntent:
    text = text or ""
    max_match = (_PRICE_MAX_RE.search(text) or _PRICE_MAX_PREFIX_RE.search(text)
                 or _PRICE_MAX_EN_RE.search(text))
    min_match = _PRICE_MIN_RE.search(text) or _PRICE_MIN_EN_RE.search(text)
    price_max = float(max_match.group(1)) if max_match else None
    price_min = float(min_match.group(1)) if min_match else None

    direction: PriceDirection | None = None
    cheap = bool(_CHEAP_RE.search(text))
    expensive = bool(_EXPENSIVE_RE.search(text))
    # 用户抱怨"当前结果"太便宜/太贵时, 意图要取反且优先级更高:
    # "太便宜了 / 嫌便宜 / 不够贵" 是在嫌弃便宜 → 实际想要贵的(太贵的情形对称)。
    # 不做这层处理, "这些太便宜了，推荐一些贵的" 会同时命中 便宜+贵 两个方向,
    # 互相抵消后没有方向 → 永远不会按贵的排序。
    too_cheap = bool(re.search(r"太便宜|嫌便宜|不够贵|不够高端", text))
    too_pricey = bool(re.search(r"太贵|嫌贵|不够便宜", text))
    if too_cheap and not too_pricey:
        direction = "expensive"
    elif too_pricey and not too_cheap:
        direction = "cheap"
    elif cheap and not expensive:
        direction = "cheap"
    elif expensive and not cheap:
        direction = "expensive"
    elif price_max is not None and price_min is None:
        direction = "cheap"
    elif price_min is not None and price_max is None:
        direction = "expensive"

    return PriceIntent(direction=direction, price_min=price_min, price_max=price_max)


def apply_price_intent(products: Sequence[dict], text: str, *, enforce_ranges: bool = True) -> list[dict]:
    """先按显式价格区间过滤, 再按价格偏好做稳定排序(stable sort)。

    输入顺序默认已经携带了检索器/reranker 给出的语义相关性,
    价格只作为最后一层偏好叠加, 不推翻语义排序。
    """
    intent = parse_price_intent(text)
    ranked = list(products)
    if not intent.active:
        return ranked

    with_index = [(i, p) for i, p in enumerate(ranked)]

    if enforce_ranges and intent.price_min is not None:
        with_index = [
            (i, p) for i, p in with_index
            if (price := _price(p)) is not None and price >= intent.price_min
        ]
    if enforce_ranges and intent.price_max is not None:
        with_index = [
            (i, p) for i, p in with_index
            if (price := _price(p)) is not None and price <= intent.price_max
        ]
    # 用户明确给出的人民币价格区间是硬约束: 返回超预算的商品等于跟用户唱反调;
    # 宁可返回空集, 让回答里直接说"没有符合条件的商品"。
    if not with_index and intent.price_min is None and intent.price_max is None:
        with_index = [(i, p) for i, p in enumerate(ranked)]

    dominant_category = _dominant_category([p for _, p in with_index])
    if intent.direction == "cheap":
        with_index.sort(
            key=lambda item: (_category_rank(item[1], dominant_category), *_price_key(item[1]), item[0])
        )
    elif intent.direction == "expensive":
        with_index.sort(
            key=lambda item: (_category_rank(item[1], dominant_category), *_price_key(item[1], descending=True), item[0])
        )

    return [p for _, p in with_index]


def _price(product: dict) -> float | None:
    """返回可比较的人民币金额; 外币商品缺少汇率(FX)换算时返回 None。"""
    raw = product.get("price_cny")
    if raw is None:
        provenance = product.get("provenance")
        currency = provenance.get("currency", "CNY") if isinstance(provenance, dict) else "CNY"
        if str(currency).upper() != "CNY":
            return None
        raw = product.get("base_price")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _price_key(product: dict, descending: bool = False) -> tuple[int, float]:
    price = _price(product)
    if price is None:
        return (1, 0.0)
    return (0, -price if descending else price)


def _dominant_category(products: Sequence[dict]) -> str | None:
    cats = [p.get("category") for p in products if p.get("category")]
    if not cats:
        return None
    category, count = Counter(cats).most_common(1)[0]
    return str(category) if count >= max(3, len(products) // 2) else None


def _category_rank(product: dict, dominant_category: str | None) -> int:
    if not dominant_category:
        return 0
    return 0 if product.get("category") == dominant_category else 1
