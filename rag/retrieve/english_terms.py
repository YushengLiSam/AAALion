"""英文购物词 → 中文品类提示词的映射, 供各检索层共用。

商品目录(及由它派生的所有关键词表)都是中文。此前英文查询只在 chat.py 的
查询增强器(query augmenter)里才能拿到中文提示词, 导致硬过滤(HARD-FILTER)推断
(rag.retrieve.constraints)看到的是原始英文文本: 提取不出 category/sub_category,
只剩价格条件的 WHERE 就返回了便宜但毫不相关的商品
("noise cancelling headphones under 1000" → 酸奶卡片)。

把映射的唯一权威来源(single source of truth)放在这里, 这样查询增强器
(server/app/routes/chat.py)和约束解析器(constraints.py)就不会再各自漂移、
出现不一致。
"""

from __future__ import annotations

import re

# 英文词 → 中文品类词。匹配时只加「前导」词边界(LEADING word boundary):
# 这样 'phone' 不会在 'headphones'/'earphones'/'iphone' 内部误触发(只让更长的词命中),
# 而 'spf50' 仍能触发 'spf'。更长/更具体的词要排在泛化词之前,
# 保证更具体的中文提示词先落位。
EN_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("noise cancelling", "降噪耳机"), ("noise-cancelling", "降噪耳机"),
    ("running shoes", "跑鞋"), ("basketball shoes", "篮球鞋"),
    ("hiking shoes", "徒步鞋"), ("hiking boots", "徒步鞋"),
    ("face wash", "洗面奶"), ("lip gloss", "唇釉"),
    ("sunscreen", "防晒霜"), ("sunblock", "防晒霜"), ("spf", "防晒霜"),
    ("lipstick", "口红"), ("moisturizer", "面霜"), ("cleanser", "洁面"),
    ("serum", "精华"), ("essence", "精华"), ("toner", "化妆水"),
    ("foundation", "粉底"), ("smartphone", "手机"), ("iphone", "iPhone 手机"),
    ("laptop", "笔记本电脑"), ("notebook", "笔记本电脑"), ("tablet", "平板"),
    ("headphones", "耳机"), ("headphone", "耳机"), ("earphones", "耳机"),
    ("earphone", "耳机"), ("earbuds", "耳机"), ("camera", "相机"),
    ("keyboard", "键盘"), ("speaker", "音箱"), ("sneakers", "运动鞋"),
    ("shoes", "鞋子"), ("hoodie", "卫衣"), ("backpack", "双肩包"), ("snacks", "零食"),
    ("snack", "零食"), ("coffee", "咖啡"), ("diaper", "纸尿裤"),
    ("toothpaste", "牙膏"), ("phone", "手机"),
)

_CJK_RE = re.compile(r"[一-鿿]")


def looks_english(text: str) -> bool:
    """文本明确是英文时返回 True: 不含 CJK 字符, 且至少有几个 ASCII 字母。
    用于选择回复语言, 以及作为下方「仅限英文」匹配逻辑的开关。"""
    if not text or _CJK_RE.search(text):
        return False
    return len(re.findall(r"[A-Za-z]", text)) >= 3


def _term_hits(low: str) -> list[str]:
    seen: set[str] = set()
    hits: list[str] = []
    for en, zh in EN_CATEGORY_HINTS:
        if zh in seen:
            continue
        if re.search(r"\b" + re.escape(en), low):
            seen.add(zh)
            hits.append(zh)
    return hits


def english_to_chinese_hints(text: str) -> list[str]:
    """针对 ``text`` 中出现的英文购物词, 返回对应的中文品类提示词列表。

    若文本本身已含中文则返回空列表——中英混排的查询自带中文信号,
    再做增强反而会让候选池产生偏差。
    """
    if not text or _CJK_RE.search(text):
        return []
    low = text.lower()
    if not any("a" <= c <= "z" for c in low):
        return []
    return _term_hits(low)


def augment_english_query(query: str, extra_text: str = "") -> str:
    """为查询中出现的英文购物词追加对应的中文品类词。

    会同时在 ``query`` 和 ``extra_text``(原始用户消息——改写后的检索查询可能
    丢掉了某个词)里查找命中词, 但只有不含中文的 ``query`` 才会被增强。
    只做「追加」(AUGMENTS), 绝不替换原查询。
    """
    if not query or _CJK_RE.search(query):
        return query
    if not any("a" <= c <= "z" for c in query.lower()):
        return query
    hits = [zh for zh in _term_hits(f"{query} {extra_text}".lower()) if zh not in query]
    return f"{query} {' '.join(hits)}".strip() if hits else query
