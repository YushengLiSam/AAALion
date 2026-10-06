"""Multi-hop 检索规划(锚定式两跳)。

单跳 RAG 回答不了"跳间依赖"的问题——第二次检索的约束来自第一次检索的**结果**:

    "有没有比 AirPods Pro 便宜的降噪耳机"
      hop1: 检索 "AirPods Pro"  → 锚点 ¥1899
      hop2: 检索 "降噪耳机" + Filter(price_max=1899*0.95, 排除锚点)

本模块只做**规划**(纯函数、无 IO):从文本解析出 HopPlan,以及把锚点商品的
结构化属性派生成 hop2 的 Filter。真正的两次检索在 `rag_client.multi_hop_retrieve`。

设计取向与 negation.py 一致:**控制流走确定性规则,不依赖 LLM 自觉**。
锚点属性直接读商品 JSON 字段(price_cny/brand/sub_category),因此 hop2 的
约束满足性可以被程序化断言(见 rag/eval 的 relation_correctness 指标)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 关系类型:决定如何把锚点属性派生成 hop2 约束
RELATION_CHEAPER = "cheaper"        # 比锚点便宜
RELATION_PRICIER = "pricier"        # 比锚点更高端
RELATION_SAME_PRICE = "same_price"  # 与锚点同价位(±20%)
RELATION_SAME_BRAND = "same_brand"  # 与锚点同品牌
RELATION_PAIR = "pair"              # 与锚点搭配(互补品类)

# 同价位带宽 / 更便宜的安全边际(0.95 避免把同价商品也算进"更便宜")
_SAME_PRICE_LO, _SAME_PRICE_HI = 0.8, 1.2
_CHEAPER_MARGIN, _PRICIER_MARGIN = 0.95, 1.05

# 搭配关系的互补品类表(锚点品类 → 建议搭配品类)。人工精选,宁缺毋滥。
PAIR_MAP: dict[str, tuple[str, ...]] = {
    "智能手机": ("无线降噪耳机", "真无线耳机", "真无线降噪耳机"),
    "平板电脑": ("无线降噪耳机", "真无线耳机"),
    "笔记本电脑": ("背包", "无线降噪耳机"),
    "跑步鞋": ("速干T恤", "短袖T恤", "运动短裤"),
    "篮球鞋": ("短袖T恤", "运动短裤"),
    "徒步鞋": ("冲锋衣", "背包", "户外裤"),
    "登山徒步鞋": ("冲锋衣", "背包", "帐篷"),
    "洁面": ("面霜", "防晒", "化妆水"),
    "防晒": ("洁面", "面霜"),
    "面霜": ("洁面", "精华", "化妆水"),
}


@dataclass
class HopPlan:
    """一次多跳检索的计划。"""
    relation: str                       # RELATION_*
    anchor_text: str                    # hop1 的检索词(如 "AirPods Pro")
    target_text: str                    # hop2 的检索词(如 "降噪耳机")
    anchor_ordinal: int | None = None   # 会话锚点:引用上一轮第 N 张卡(1-based, -1=最后)
    raw: str = ""                       # 原始 query(调试/trace 用)

    @property
    def uses_history_anchor(self) -> bool:
        return self.anchor_ordinal is not None


# ---------------------------------------------------------------------------
# 触发模式
# ---------------------------------------------------------------------------
# 会话锚点:"比刚才第二款便宜的" —— 锚点在上一轮卡片里,不需要 hop1 检索
_HISTORY_ANCHOR_RE = re.compile(
    r"比\s*(?:刚才|刚刚|上面|前面|上边)?\s*(?:那|这)?\s*"
    r"(?:第\s*([0-9一二两三四五六七八九十]+)\s*(?:个|款|件)?|(?:这|那)(?:个|款|件))"
    r"[^，。,\s]*?(便宜|贵|好|高端)"
)
_LAST_ANCHOR_RE = re.compile(r"比\s*(?:刚才|刚刚|上面|前面)(?:那|这)?(?:个|款|件)?[^，。,\s]*?(便宜|贵|高端)")

# 比较锚定:"比 X 便宜的 Y" / "有没有比 X 更便宜的 Y"
_COMPARE_RE = re.compile(
    r"比\s*(?P<anchor>[^,，。;；!!??]{2,24}?)\s*"
    r"(?:更|再|还)?\s*(?P<rel>便宜|实惠|划算|贵|高端)"
    r"(?:\s*(?:一点|一些|点))?"
    r"(?:\s*的)?\s*(?P<target>[^,，。;；!!??\s]{0,12})"
)

# 同类锚定:"跟 X 同价位的 Y" / "和 X 一样牌子的 Y"
_SIMILAR_RE = re.compile(
    r"(?:跟|和|与)\s*(?P<anchor>[^,，。;；!!??]{2,24}?)\s*"
    r"(?:差不多|一样|相同|同)\s*(?P<dim>价位|价格|档次|品牌|牌子)"
    r"(?:\s*的)?\s*(?P<target>[^,，。;；!!??\s]{0,12})"
)

# 搭配锚定:"买了 X,配个 Y" / "刚入了 X 想搭个 Y"
_PAIR_RE = re.compile(
    r"(?:买了|入了|刚入|有了|用的是)\s*(?P<anchor>[^,，。;；!!??]{2,24}?)"
    r"\s*[,，]?\s*(?:再|想|想要)?\s*(?:配|搭|搭配)\s*(?:个|双|条|件|套)?\s*"
    r"(?P<target>[^,，。;；!!??\s]{0,12})"
)

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 目标词兜底:target 抽空时按锚点品类推断(在 rag_client 里用锚点 sub_category)
_TARGET_STOPWORDS = {"的", "吗", "呢", "有", "没有", "还有", "其他", "别的", "款", "个"}


def _to_int(token: str) -> int | None:
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    return _CN_NUM.get(token)


def _clean_target(raw: str) -> str:
    t = (raw or "").strip()
    for w in _TARGET_STOPWORDS:
        if t == w:
            return ""
    return t


def _known_catalog_terms() -> set[str]:
    """目录里可作为锚点的词:品牌名 + 品牌别名。与 except_brands 同源,
    保证"锚点必须能在目录里对上"才触发多跳,零误伤单跳流程。"""
    terms: set[str] = set()
    try:
        from rag.retrieve.brand_origin import BRAND_ORIGIN
        for b in BRAND_ORIGIN:
            if b:
                terms.add(b.casefold())
    except Exception:
        pass
    try:
        from rag.retrieve.constraints import _catalog_brands
        for b in _catalog_brands():
            if b:
                terms.add(b.casefold())
    except Exception:
        pass
    return terms


def _anchor_is_groundable(anchor: str) -> bool:
    """锚点词能否落到目录里(品牌名/别名/含产品线关键词)。
    保守:对不上就不触发多跳,回退现有单跳流程。"""
    if not anchor or len(anchor) < 2:
        return False
    a = anchor.casefold()
    for term in _known_catalog_terms():
        if len(term) >= 2 and (term in a or a in term):
            return True
    # 品类词也可作锚点("买了跑鞋配个T恤" —— 锚点是品类不是品牌)
    try:
        from rag.retrieve.constraints import _SUB_CATEGORY_RULES
        for terms, _subs in _SUB_CATEGORY_RULES:
            for t in terms:
                if len(t) >= 2 and t.casefold() in a:
                    return True
    except Exception:
        pass
    # 常见产品线名(目录里以标题形式存在,不在品牌表里)
    for line in ("iphone", "ipad", "macbook", "airpods", "switch", "mate", "pura",
                 "freebuds", "watch", "thinkpad", "matebook", "pixel", "reno",
                 "find", "mix", "ultraboost", "pegasus", "clifton", "samba",
                 "air max", "kt", "160x", "神仙水", "小黑瓶", "小棕瓶", "红腰子"):
        if line in a:
            return True
    return False


def detect_multihop(text: str, *, has_history_cards: bool = False) -> HopPlan | None:
    """从 query 解析多跳计划;不是多跳则返回 None(调用方走原单跳流程)。

    `has_history_cards`:上一轮是否有商品卡。为 True 时才允许会话锚点
    ("比刚才第二款便宜的")。
    """
    if not text or not text.strip():
        return None
    t = text.strip()

    # 1) 会话锚点(优先:它的 "比…便宜" 也会被 _COMPARE_RE 命中,需先判)
    if has_history_cards:
        m = _HISTORY_ANCHOR_RE.search(t)
        if m:
            ordinal = _to_int(m.group(1) or "") or -1
            rel = RELATION_CHEAPER if m.group(2) in ("便宜",) else (
                RELATION_PRICIER if m.group(2) in ("贵", "高端") else RELATION_CHEAPER)
            return HopPlan(relation=rel, anchor_text="", target_text="",
                           anchor_ordinal=ordinal, raw=t)
        m = _LAST_ANCHOR_RE.search(t)
        if m:
            rel = RELATION_CHEAPER if m.group(1) == "便宜" else RELATION_PRICIER
            return HopPlan(relation=rel, anchor_text="", target_text="",
                           anchor_ordinal=-1, raw=t)

    # 2) 搭配锚定(先于比较:"买了X配个Y" 里没有"比")
    m = _PAIR_RE.search(t)
    if m:
        anchor = m.group("anchor").strip()
        if _anchor_is_groundable(anchor):
            return HopPlan(relation=RELATION_PAIR, anchor_text=anchor,
                           target_text=_clean_target(m.group("target")), raw=t)

    # 3) 比较锚定
    m = _COMPARE_RE.search(t)
    if m:
        anchor = m.group("anchor").strip()
        if _anchor_is_groundable(anchor):
            rel = RELATION_CHEAPER if m.group("rel") in ("便宜", "实惠", "划算") else RELATION_PRICIER
            return HopPlan(relation=rel, anchor_text=anchor,
                           target_text=_clean_target(m.group("target")), raw=t)

    # 4) 同类锚定
    m = _SIMILAR_RE.search(t)
    if m:
        anchor = m.group("anchor").strip()
        if _anchor_is_groundable(anchor):
            dim = m.group("dim")
            rel = RELATION_SAME_BRAND if dim in ("品牌", "牌子") else RELATION_SAME_PRICE
            return HopPlan(relation=rel, anchor_text=anchor,
                           target_text=_clean_target(m.group("target")), raw=t)

    return None


def _source_currency(product: dict) -> str:
    """商品源币种;与 currency._product_currency 同规则(缺 provenance 视为 CNY)。"""
    prov = product.get("provenance")
    raw = prov.get("currency") if isinstance(prov, dict) else None
    return str(raw or "CNY").upper().strip()


def anchor_attrs(product: dict) -> dict:
    """从锚点商品里提取结构化属性(不经过 LLM,因此可断言)。

    price_cny **只接受人民币**:优先用已归一化的 price_cny;没有时仅当源币种
    本身是 CNY 才回落到 base_price。外币商品若还没做汇率归一化,价格记为 None
    (调用方 rag_client.multi_hop_retrieve 会先归一化锚点再调用本函数)。
    旧实现直接拿外币 base_price 兜底——海外版 AirPods Pro 2 的 $249 被当成 ¥249,
    "比它便宜"的上限被算错(多跳 Bug 1)。
    """
    if not product:
        return {}
    price = product.get("price_cny")
    if price is None and _source_currency(product) == "CNY":
        price = product.get("base_price")
    return {
        "product_id": product.get("product_id"),
        "title": product.get("title"),
        "brand": product.get("brand"),
        "category": product.get("category"),
        "sub_category": product.get("sub_category"),
        "price_cny": float(price) if price is not None else None,
    }


def derive_filter(attrs: dict, relation: str, *, target_sub_categories: list[str] | None = None):
    """把锚点属性 + 关系 → hop2 的 Filter(确定性映射)。"""
    from rag.retrieve.query import Filter

    f = Filter()
    price = attrs.get("price_cny")
    sub = attrs.get("sub_category")

    if relation == RELATION_CHEAPER and price:
        f.price_max_cny = round(price * _CHEAPER_MARGIN, 2)
        f.sub_categories = target_sub_categories or ([sub] if sub else None)
    elif relation == RELATION_PRICIER and price:
        f.price_min_cny = round(price * _PRICIER_MARGIN, 2)
        f.sub_categories = target_sub_categories or ([sub] if sub else None)
    elif relation == RELATION_SAME_PRICE and price:
        f.price_min_cny = round(price * _SAME_PRICE_LO, 2)
        f.price_max_cny = round(price * _SAME_PRICE_HI, 2)
        f.sub_categories = target_sub_categories or ([sub] if sub else None)
    elif relation == RELATION_SAME_BRAND:
        brand = attrs.get("brand")
        if brand:
            f.brand_include = [brand]
        if target_sub_categories:
            f.sub_categories = target_sub_categories
    elif relation == RELATION_PAIR:
        subs = target_sub_categories or list(PAIR_MAP.get(sub or "", ()))
        if subs:
            f.sub_categories = subs
    return f if f.active else None


def relation_label(relation: str, attrs: dict) -> str:
    """给 UI / prompt 用的人话描述。"""
    price = attrs.get("price_cny")
    title = (attrs.get("title") or "")[:20]
    if relation == RELATION_CHEAPER:
        return f"比「{title}」(¥{price:.0f})更便宜" if price else f"比「{title}」更便宜"
    if relation == RELATION_PRICIER:
        return f"比「{title}」(¥{price:.0f})更高端" if price else f"比「{title}」更高端"
    if relation == RELATION_SAME_PRICE:
        return f"与「{title}」同价位(¥{price:.0f}±20%)" if price else f"与「{title}」同价位"
    if relation == RELATION_SAME_BRAND:
        return f"与「{title}」同品牌({attrs.get('brand')})"
    if relation == RELATION_PAIR:
        return f"与「{title}」搭配"
    return relation
