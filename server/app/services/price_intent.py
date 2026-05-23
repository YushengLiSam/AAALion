"""Lightweight price-intent parsing and candidate ordering."""

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


_CHEAP_RE = re.compile(r"便宜|平价|性价比|低价|划算|预算有限|预算友好")
_EXPENSIVE_RE = re.compile(r"贵|高端|高价|旗舰|预算充足|好一点")
_PRICE_MAX_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以内|以下|内|封顶|不超过|不要超过)")
_PRICE_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以上|起|起步)")


def parse_price_intent(text: str) -> PriceIntent:
    text = text or ""
    max_match = _PRICE_MAX_RE.search(text)
    min_match = _PRICE_MIN_RE.search(text)
    price_max = float(max_match.group(1)) if max_match else None
    price_min = float(min_match.group(1)) if min_match else None

    direction: PriceDirection | None = None
    cheap = bool(_CHEAP_RE.search(text))
    expensive = bool(_EXPENSIVE_RE.search(text))
    if cheap and not expensive:
        direction = "cheap"
    elif expensive and not cheap:
        direction = "expensive"
    elif price_max is not None and price_min is None:
        direction = "cheap"
    elif price_min is not None and price_max is None:
        direction = "expensive"

    return PriceIntent(direction=direction, price_min=price_min, price_max=price_max)


def apply_price_intent(products: Sequence[dict], text: str) -> list[dict]:
    """Filter explicit price ranges, then stable-sort by price preference.

    The input order is assumed to already encode semantic relevance from the
    retriever/reranker. Price is only a final preference layer.
    """
    intent = parse_price_intent(text)
    ranked = list(products)
    if not intent.active:
        return ranked

    with_index = [(i, p) for i, p in enumerate(ranked)]

    if intent.price_min is not None:
        with_index = [(i, p) for i, p in with_index if _price(p) >= intent.price_min]
    if intent.price_max is not None:
        with_index = [(i, p) for i, p in with_index if _price(p) <= intent.price_max]
    if not with_index:
        with_index = [(i, p) for i, p in enumerate(ranked)]

    dominant_category = _dominant_category([p for _, p in with_index])
    if intent.direction == "cheap":
        with_index.sort(key=lambda item: (_category_rank(item[1], dominant_category), _price(item[1]), item[0]))
    elif intent.direction == "expensive":
        with_index.sort(key=lambda item: (_category_rank(item[1], dominant_category), -_price(item[1]), item[0]))

    return [p for _, p in with_index]


def _price(product: dict) -> float:
    raw = product.get("base_price")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


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
