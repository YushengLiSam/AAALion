"""Parse positive shopping constraints into retrieval-time metadata filters.

These are hard constraints such as product type, named brand, and RMB budget.
Semantic preferences ("适合熬夜", "性价比") remain in the query/reranker.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from rag.retrieve.query import Filter

REPO_ROOT = Path(__file__).resolve().parents[2]

_PRICE_MAX_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以内|以下|内|封顶|不超过|不要超过)")
_PRICE_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以上|起|起步)")
_BUDGET_MAX_RE = re.compile(
    r"(?:预算|价格上限|最高价)\s*(?:提高|增加|加|放宽|调整|调|改)?\s*"
    r"(?:到|至|为|成)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
    re.IGNORECASE,
)
_NEGATED_PREFIX_RE = re.compile(r"(?:不要|不选|不买|排除|除了|避开|no\s*|without\s*)[^，。；,;]*$", re.IGNORECASE)

_DIRECT_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("美妆护肤", ("美妆护肤", "护肤品", "护肤")),
    ("数码电子", ("数码电子", "数码产品")),
    ("服饰运动", ("服饰运动", "运动服饰")),
    ("食品饮料", ("食品饮料",)),
    ("食品生活", ("食品生活",)),
    ("母婴健康", ("母婴健康", "母婴")),
    ("家居家具", ("家居家具", "家居")),
    ("图书音像", ("图书音像", "图书")),
    ("户外运动", ("户外运动", "户外")),
)

# Only infer a category where current catalog membership is unambiguous.
_INFERRED_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("美妆护肤", ("洗面奶", "洁面", "防晒", "面霜", "精华", "化妆水", "爽肤水")),
    ("数码电子", ("耳机", "手机", "折叠屏", "笔记本", "平板", "相机")),
    ("图书音像", ("小说",)),
    ("食品饮料", ("速溶咖啡", "咖啡", "牛奶")),
)

# R8.F.8 (expanded in R8.F.8.1): topic-switch ONLY keywords. These are
# checked by rag_client's topic-switch detector to recognize a domain
# pivot in the user's raw message. NOT used by hard filtering — adding
# them above would narrow retrieval too aggressively on single-turn
# queries.
TOPIC_SWITCH_HINTS: dict[str, tuple[str, ...]] = {
    "美妆护肤": ("美妆", "化妆品", "彩妆", "口红"),
    "数码电子": ("电脑", "智能手表", "音箱"),
    "服饰运动": ("运动鞋", "跑鞋", "T恤", "外套", "裤子", "衣服", "夹克", "鞋",
                  "鞋子", "上衣", "短裤"),
    "食品饮料": ("饮料", "茶", "矿泉水"),
    "食品生活": ("零食", "巧克力", "饼干", "薯片", "洗衣液", "牙膏", "酱油"),
    "母婴健康": ("奶粉", "尿不湿", "纸尿裤", "纸尿片", "婴儿用品", "童装", "母婴"),
    "家居家具": ("沙发", "床", "餐具", "桌椅", "家具"),
    "图书音像": ("书", "教材"),
}


def detect_topic_switch_category(text: str) -> str | None:
    """Return a category if the raw text contains a switch-hint keyword.
    Used by the multi-turn topic-switch detector — does NOT participate
    in hard filter construction (which would over-narrow single-turn queries).
    """
    if not text:
        return None
    for cat, words in TOPIC_SWITCH_HINTS.items():
        if any(w in text for w in words):
            return cat
    return None

# A user concept may span several source sub-categories.
_SUB_CATEGORY_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("洗面奶", "洁面"), ["洁面"]),
    (("高倍防晒", "防晒"), ["防晒"]),
    (("面霜",), ["面霜", "面霜/敏感肌"]),
    (("化妆水", "爽肤水"), ["化妆水", "化妆水/精华水", "爽肤水/化妆水", "精华水"]),
    (("精华液", "精华"), ["精华", "精华液"]),
    (("头戴式降噪耳机", "蓝牙耳机", "无线耳机", "降噪耳机", "耳机"), ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]),
    (("笔记本电脑", "笔记本"), ["笔记本电脑"]),
    (("折叠屏", "手机"), ["智能手机"]),
    (("平板",), ["平板电脑"]),
    (("相机",), ["口袋摄像机"]),
    (("运动T恤", "运动t恤", "速干T恤", "速干t恤"), ["短袖T恤", "速干T恤"]),
    (("登山鞋", "徒步鞋"), ["徒步鞋", "登山徒步鞋"]),
    (("跑步鞋", "跑鞋"), ["跑步鞋", "运动休闲鞋"]),
    (("双肩包", "背包"), ["背包"]),
    (("智能手表", "运动手表"), ["运动手表"]),
    (("小说",), ["中文小说/科幻", "中文小说/经典"]),
    (("速溶咖啡", "咖啡"), ["咖啡"]),
    (("休闲零食", "零食"), ["坚果/零食", "进口巧克力零食", "进口零食饼干"]),
    (("牛奶",), ["牛奶"]),
)


def build_retrieval_filter(text: str, explicit: Mapping[str, Any] | None = None) -> Filter | None:
    """Infer a filter from query text, then apply explicit API overrides."""
    result = Filter(
        category=_category(text),
        sub_categories=_sub_categories(text),
        brand_include=[],
        brand_exclude=[],
        price_max_cny=_price_bound(_PRICE_MAX_RE, text) or _price_bound(_BUDGET_MAX_RE, text),
        price_min_cny=_price_bound(_PRICE_MIN_RE, text),
    )
    included, excluded = _brands(text)
    result.brand_include = included or None
    result.brand_exclude = excluded or None

    # R8: extract country-trigger keywords ("日系" / "美系" / ...) locally
    # so they persist across multi-turn conversations via Filter state.
    # `apply_negation` resolves these to ISO codes through brand_origin.
    if text and any(neg in text for neg in ("不要", "不含", "不带", "除了", "排除", "也不要")):
        try:
            from rag.retrieve.negation import _local_country_keywords
            result.exclude_keywords = _local_country_keywords(text) or None
        except Exception:
            result.exclude_keywords = None

    if explicit:
        if explicit.get("category") is not None:
            result.category = str(explicit["category"])
        if explicit.get("sub_category") is not None:
            result.sub_category = str(explicit["sub_category"])
            result.sub_categories = None
        if explicit.get("sub_categories") is not None:
            result.sub_category = None
            result.sub_categories = list(explicit["sub_categories"]) or None
        if explicit.get("include_brands") is not None:
            result.brand_include = list(explicit["include_brands"]) or None
        if explicit.get("exclude_brands") is not None:
            result.brand_exclude = list(explicit["exclude_brands"]) or None
        if explicit.get("price_min") is not None:
            result.price_min_cny = float(explicit["price_min"])
        if explicit.get("price_max") is not None:
            result.price_max_cny = float(explicit["price_max"])
    return result if result.active else None


def _category(text: str) -> str | None:
    for category, terms in _DIRECT_CATEGORIES:
        if any(term in text for term in terms):
            return category
    for category, terms in _INFERRED_CATEGORIES:
        if any(term in text for term in terms):
            return category
    return None


def _sub_categories(text: str) -> list[str] | None:
    for terms, values in _SUB_CATEGORY_RULES:
        if any(term in text for term in terms):
            return list(values)
    return None


def _price_bound(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text or "")
    return float(match.group(1)) if match else None


@lru_cache(maxsize=1)
def _catalog_brands() -> tuple[str, ...]:
    brands: set[str] = set()
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            product = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        brand = product.get("brand")
        if isinstance(brand, str) and brand.strip():
            brands.add(brand.strip())
    return tuple(sorted(brands, key=len, reverse=True))


def _brand_terms(catalog_brand: str) -> set[str]:
    from rag.retrieve.brand_origin import BRAND_ALIASES

    lowered = catalog_brand.casefold()
    terms = {lowered}
    for cluster in BRAND_ALIASES:
        if any(alias in lowered or lowered in alias for alias in cluster):
            terms.update(alias.casefold() for alias in cluster)
    return {term for term in terms if len(term) >= 2}


def _brands(text: str) -> tuple[list[str], list[str]]:
    lowered = (text or "").casefold()
    included: list[str] = []
    excluded: list[str] = []
    for catalog_brand in _catalog_brands():
        positions = [
            lowered.find(term)
            for term in _brand_terms(catalog_brand)
            if lowered.find(term) >= 0
        ]
        if not positions:
            continue
        is_excluded = any(_is_negated(lowered, position) for position in positions)
        target = excluded if is_excluded else included
        target.append(catalog_brand)
    excluded_set = set(excluded)
    return ([brand for brand in included if brand not in excluded_set], excluded)


def _is_negated(text: str, position: int) -> bool:
    prefix = text[max(0, position - 18):position]
    if _NEGATED_PREFIX_RE.search(prefix):
        return True
    # R11.fix — "X以外 / X之外" (e.g. multi-turn "华为以外还有吗"): the brand is
    # the thing being EXCLUDED, but the marker is a SUFFIX, so without this it
    # was mis-read as a positive brand_include (→ retrieve only 华为). Look a
    # short window past the brand for the 以外/之外 marker.
    suffix = text[position:position + 10]
    return "以外" in suffix or "之外" in suffix
