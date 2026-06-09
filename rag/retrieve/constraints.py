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
# R12 — English budget phrasing ("under ¥500", "below 500", "≤ 1000") so
# English-mode queries and quick-reply chips filter by price too.
_PRICE_MAX_EN_RE = re.compile(
    r"(?:under|below|less than|within|up to|no more than|cheaper than|max\.?|≤|<=?)\s*"
    r"[¥￥$]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_NEGATED_PREFIX_RE = re.compile(
    r"(?:不想要|不需要|不要|别要|别给我|不考虑|不选|不买|排除|除了|避开|no\s*|without\s*)[^，。；,;的]*$",
    re.IGNORECASE,
)
# SUFFIX dismissal: the brand comes FIRST, then a clause-final brush-off
# ("小米的就算了" / "苹果的就不看了" / "安热沙的不要"). Distinct from the prefix
# forms above and from 以外/之外. Applied to the text right AFTER the brand.
_SUFFIX_DISMISS_RE = re.compile(
    r"^的?\s*(?:这个|那个|那款|的话)?\s*(?:就)?"
    r"(?:算了|不用了?|不考虑了?|不看了?|不喜欢|跳过|pass|不行了?|不要了)"
)
# Bare suffix "X的不要" — only when clause-final, so "苹果的不要太贵" (price
# modifier) does NOT wrongly exclude the positively-asked brand.
_SUFFIX_BUYAO_RE = re.compile(r"^的?\s*不要\s*(?=[，。,；;]|$)")


def _is_suffix_negated(text: str, end: int) -> bool:
    window = text[end:end + 12]
    return bool(_SUFFIX_DISMISS_RE.match(window) or _SUFFIX_BUYAO_RE.match(window))

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

# Backstop category inference: pins a query to its aisle so a term WITHOUT a
# precise sub-category rule (e.g. 球衣 / 运动服 / 香薰) still stays in-category
# instead of leaking across all 145 products. Only single-category terms are
# listed; cross-aisle sport terms (跑鞋/徒步鞋, which span 服饰运动+户外运动) are
# intentionally absent so their sub-category rule can return both aisles.
_INFERRED_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("美妆护肤", ("洗面奶", "洁面", "防晒", "面霜", "精华", "化妆水", "爽肤水", "神仙水",
                  "口红", "唇釉", "唇膏", "唇彩", "粉底", "散粉", "蜜粉", "眼霜", "眉笔",
                  "卸妆", "面膜", "护肤", "彩妆")),
    ("数码电子", ("耳机", "手机", "折叠屏", "笔记本", "平板", "相机", "摄像机",
                  "游戏机", "游戏主机", "任天堂", "switch", "电脑",
                  # R12.bugfix — English/brand product-line names so a single
                  # query OR a multi-turn opener like "推荐 iPhone" pins to
                  # 数码电子 and follow-ups inherit the aisle. Match is
                  # casefold-insensitive (see _category), so lowercase is fine.
                  "iphone", "ipad", "airpods", "macbook", "apple watch")),
    ("服饰运动", ("卫衣", "羽绒", "球衣", "球服", "运动服", "运动上衣", "运动外套",
                  "牛仔裤", "瑜伽裤", "篮球鞋", "T恤", "上衣", "衣服", "服装",
                  "帽子", "卫裤", "短袖", "外套", "夹克")),
    ("母婴健康", ("奶粉", "纸尿裤", "尿不湿", "辅食", "米粉", "孕妇", "叶酸",
                  "蛋白粉", "婴儿", "母婴", "奶瓶")),
    ("家居家具", ("四件套", "床上用品", "床品", "被子", "枕头", "插线板", "插排",
                  "排插", "香薰", "香氛", "家居", "家具")),
    ("图书音像", ("小说", "漫画", "字典", "词典", "工具书", "科幻", "名著")),
    ("食品饮料", ("速溶咖啡", "咖啡", "牛奶", "酸奶", "泡面", "方便面", "功能饮料",
                  "碳酸饮料", "气泡水", "茶饮", "调味品", "酱油")),
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
# Ordered specific → generic: _sub_categories returns the FIRST rule with any
# matching term, so a specific term (篮球鞋) must precede a generic one. Built
# from the live catalog taxonomy (9 categories / 73 sub_categories) so a
# category query narrows to its own aisle instead of leaking across categories.
_SUB_CATEGORY_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    # —— 美妆护肤 ——
    (("口红", "唇釉", "唇膏", "唇彩"), ["唇釉"]),
    (("卸妆油", "卸妆水", "卸妆膏", "卸妆"), ["卸妆"]),
    (("洗面奶", "洁面乳", "洁面"), ["洁面"]),
    (("高倍防晒", "防晒霜", "防晒乳", "防晒"), ["防晒"]),
    (("眼霜", "眼部精华"), ["眼霜"]),
    (("眉笔", "眉粉"), ["眉笔"]),
    (("粉底液", "粉底", "气垫"), ["粉底液"]),
    (("蜜粉", "散粉", "定妆粉", "粉饼"), ["蜜粉"]),
    (("面膜",), ["面膜"]),
    (("面霜", "乳霜", "晚霜", "日霜"), ["面霜", "面霜/敏感肌"]),
    (("神仙水", "爽肤水", "化妆水", "柔肤水", "水乳"), ["化妆水", "化妆水/精华水", "爽肤水/化妆水", "精华水"]),
    (("精华液", "精华", "小黑瓶", "小棕瓶", "红腰子", "肌底液"), ["精华", "精华液", "精华水"]),
    # —— 数码电子 ——
    (("头戴式降噪耳机", "蓝牙耳机", "无线耳机", "降噪耳机", "耳机"), ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]),
    (("笔记本电脑", "笔记本"), ["笔记本电脑"]),
    (("折叠屏", "智能手机", "手机"), ["智能手机"]),
    (("平板电脑", "平板"), ["平板电脑"]),
    (("摄像机", "口袋相机", "云台相机", "运动相机", "相机"), ["口袋摄像机"]),
    (("游戏主机", "游戏机", "掌机", "switch", "任天堂"), ["游戏主机"]),
    # —— 服饰运动 / 户外运动(部分 sub 跨这两个类目)——
    (("连帽卫衣", "连帽衫", "帽衫", "卫衣"), ["卫衣"]),
    (("鸭舌帽", "棒球帽", "球帽", "帽子"), ["帽子"]),
    (("羽绒服", "羽绒衣", "羽绒"), ["羽绒服"]),
    (("抓绒外套", "抓绒", "软壳外套"), ["抓绒外套"]),
    (("冲锋衣", "三合一外套"), ["冲锋衣"]),
    (("牛仔裤",), ["牛仔裤"]),
    (("瑜伽裤", "紧身裤", "打底裤"), ["瑜伽裤"]),
    (("户外裤", "软壳裤"), ["户外裤"]),
    (("运动短裤", "短裤"), ["运动短裤"]),
    (("运动长裤", "卫裤", "束脚裤", "长裤"), ["运动长裤"]),
    (("运动裤", "休闲裤"), ["运动长裤", "运动短裤"]),
    (("速干T恤", "运动T恤", "运动t恤", "速干t恤", "T恤", "t恤", "短袖"), ["短袖T恤", "速干T恤"]),
    (("篮球鞋",), ["篮球鞋"]),
    (("跑步鞋", "跑鞋", "马拉松鞋"), ["跑步鞋"]),
    (("板鞋", "休闲鞋", "复古鞋"), ["运动休闲鞋"]),
    (("登山鞋", "徒步鞋", "登山徒步鞋"), ["徒步鞋", "登山徒步鞋"]),
    (("帐篷", "露营"), ["帐篷"]),
    (("双肩包", "背包", "登山包", "书包"), ["背包"]),
    (("智能手表", "运动手表", "手表"), ["运动手表"]),
    # —— 母婴健康 ——
    (("婴儿奶粉", "婴幼儿奶粉", "配方奶", "奶粉"), ["婴幼儿奶粉"]),
    (("婴儿辅食", "辅食", "米粉"), ["婴儿辅食"]),
    (("孕妇营养", "叶酸", "孕妇"), ["孕妇营养品"]),
    (("纸尿裤", "尿不湿", "尿布", "拉拉裤"), ["纸尿裤"]),
    (("蛋白粉",), ["蛋白粉"]),
    # —— 家居家具 ——
    (("四件套", "床上用品", "床品", "被套", "被子", "枕头"), ["床上用品"]),
    (("插线板", "插排", "排插", "接线板", "插座"), ["插线板"]),
    (("香薰", "香氛", "家居香氛", "扩香"), ["礼盒/家居香氛"]),
    (("医用冰箱", "家用健康家电", "家电"), ["家用健康家电"]),
    # —— 食品饮料 ——
    (("功能饮料", "能量饮料"), ["功能饮料"]),
    (("速溶咖啡", "咖啡"), ["咖啡"]),
    (("方便面", "泡面", "拌面", "方便食品"), ["方便食品"]),
    (("酸奶",), ["酸奶"]),
    (("牛奶",), ["牛奶"]),
    (("碳酸饮料", "汽水", "可乐", "气泡水", "苏打水", "苏打"), ["碳酸饮料"]),
    (("茶饮", "茶"), ["茶饮"]),
    (("调味品", "酱油", "生抽", "老抽", "蚝油"), ["调味品"]),
    # —— 食品生活(进口)——
    (("巧克力",), ["进口巧克力零食"]),
    (("饼干",), ["进口零食饼干"]),
    (("蛋黄酱", "沙拉酱", "酱料"), ["进口酱料调味"]),
    (("调味料", "百吉饼"), ["进口调味料"]),
    (("乳酸菌饮料", "乳酸饮料", "可尔必思"), ["进口饮料"]),
    (("坚果", "休闲零食", "零食"), ["坚果/零食", "进口巧克力零食", "进口零食饼干"]),
    # —— 图书音像 ——
    (("科幻小说", "科幻", "三体"), ["中文小说/科幻"]),
    (("名著", "经典文学", "活着", "平凡的世界"), ["中文小说/经典"]),
    (("小说", "文学"), ["中文小说/科幻", "中文小说/经典"]),
    (("字典", "词典", "工具书"), ["工具书"]),
    (("漫画", "哆啦", "连环画"), ["漫画"]),
)


def build_retrieval_filter(text: str, explicit: Mapping[str, Any] | None = None) -> Filter | None:
    """Infer a filter from query text, then apply explicit API overrides."""
    result = Filter(
        category=_category(text),
        sub_categories=_sub_categories(text),
        brand_include=[],
        brand_exclude=[],
        price_max_cny=(_price_bound(_PRICE_MAX_RE, text) or _price_bound(_BUDGET_MAX_RE, text)
                       or _price_bound(_PRICE_MAX_EN_RE, text)),
        price_min_cny=_price_bound(_PRICE_MIN_RE, text),
    )
    included, excluded = _brands(text)
    result.brand_include = included or None
    result.brand_exclude = excluded or None

    # Category-conflict guard: a scene/attribute word can infer a category that
    # contradicts the more-specific sub_category or the named brands — e.g.
    # "对比防晒...哪个更适合户外" infers 户外运动 while 防晒 is 美妆护肤, so the AND
    # filter (category × sub) returns 0. The specific signal wins: drop the
    # inferred category when the catalog has no (category, sub_category) pair for
    # it, or (absent subs) none of the included brands live in it.
    if result.category:
        pairs = _catalog_cat_subcats()
        bcats = _catalog_brand_cats()
        sub_conflict = bool(result.sub_categories) and not any(
            (result.category, sc) in pairs for sc in result.sub_categories
        )
        brand_conflict = bool(result.brand_include) and not any(
            result.category in bcats.get(str(b).casefold(), frozenset()) for b in result.brand_include
        )
        if sub_conflict or (not result.sub_categories and brand_conflict):
            result.category = None

    # R8: extract country-trigger keywords ("日系" / "美系" / ...) locally
    # so they persist across multi-turn conversations via Filter state.
    # `apply_negation` resolves these to ISO codes through brand_origin.
    if text and any(neg in text for neg in (
        "不要", "别要", "别给我", "不想要", "不需要", "不考虑", "不含", "不带",
        "除了", "排除", "也不要", "就算了", "就不看", "不用了"
    )):
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
    # casefold so English/brand keywords ("iphone", "switch", "T恤") match
    # regardless of the casing the user typed ("iPhone", "Switch", "iPHONE").
    low = (text or "").casefold()
    for category, terms in _DIRECT_CATEGORIES:
        if any(term.casefold() in low for term in terms):
            return category
    for category, terms in _INFERRED_CATEGORIES:
        if any(term.casefold() in low for term in terms):
            return category
    return None


def _sub_categories(text: str) -> list[str] | None:
    low = (text or "").casefold()
    for terms, values in _SUB_CATEGORY_RULES:
        if any(term.casefold() in low for term in terms):
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


@lru_cache(maxsize=1)
def _catalog_cat_subcats() -> frozenset[tuple[str, str]]:
    """Valid (category, sub_category) pairs that actually exist in the catalog —
    used to detect when an inferred category contradicts a more-specific
    sub_category (e.g. '防晒' is 美妆护肤, never 户外运动)."""
    pairs: set[tuple[str, str]] = set()
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        c, s = p.get("category"), p.get("sub_category")
        if isinstance(c, str) and isinstance(s, str):
            pairs.add((c.strip(), s.strip()))
    return frozenset(pairs)


@lru_cache(maxsize=1)
def _catalog_brand_cats() -> dict[str, frozenset[str]]:
    """brand (casefold) → set of categories it appears in."""
    out: dict[str, set[str]] = {}
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        b, c = p.get("brand"), p.get("category")
        if isinstance(b, str) and isinstance(c, str):
            out.setdefault(b.strip().casefold(), set()).add(c.strip())
    return {k: frozenset(v) for k, v in out.items()}


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
        hits = [
            (lowered.find(term), len(term))
            for term in _brand_terms(catalog_brand)
            if lowered.find(term) >= 0
        ]
        if not hits:
            continue
        # Excluded if any occurrence is negated by a prefix ("不要X"/"别给我X"),
        # the 以外/之外 suffix, or a clause-final brush-off ("X的就算了"/"X的不要").
        is_excluded = any(
            _is_negated(lowered, pos) or _is_suffix_negated(lowered, pos + tlen)
            for pos, tlen in hits
        )
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
