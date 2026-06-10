"""检索查询用的人工精选电商同义词扩展。

这个运行时词典要保持小而精(高准确率)。粗放的同义词工具可以离线配合
``tools/build_synonym_candidates.py`` 来产出候选词条,但生产检索只允许
使用我们人工审核过的词条。
"""

from __future__ import annotations

MAX_EXTRA_TERMS = 8
PRICE_INTENT_SIGNALS = (
    "便宜",
    "平价",
    "性价比",
    "预算",
    "低价",
    "划算",
    "贵",
    "高端",
    "高价",
    "多少钱",
    "元",
    "以下",
    "以内",
    "以上",
)

SYNONYM_TERMS: dict[str, tuple[str, ...]] = {
    # 数码产品。
    "无线耳机": ("蓝牙耳机", "真无线耳机", "TWS", "头戴耳机", "降噪耳机"),
    "蓝牙耳机": ("无线耳机", "真无线耳机", "TWS", "入耳式耳机", "头戴耳机"),
    "耳机": ("蓝牙耳机", "无线耳机", "真无线耳机", "头戴耳机"),
    "降噪": ("主动降噪", "ANC", "noise cancelling", "降噪耳机"),
    # 美妆与护肤。
    "洁面": ("洗面奶", "洁面乳", "洁面产品", "清洁"),
    "洗面奶": ("洁面", "洁面乳", "洁面产品", "清洁"),
    "舒敏": ("敏感肌", "舒缓", "修护", "屏障修护"),
    "敏感肌": ("舒敏", "舒缓", "修护", "屏障修护"),
    "控油": ("油皮", "油性肌肤", "清爽", "抑油"),
    "油皮": ("控油", "油性肌肤", "清爽", "抑油"),
    "精华": ("精华液", "修护精华", "抗氧化", "熬夜肌"),
    "熬夜": ("暗沉", "修护", "抗氧化", "提亮"),
    "防晒": ("防晒霜", "防晒乳", "SPF", "清爽防晒"),
    "保湿": ("补水", "滋润", "修护屏障", "面霜"),
    # 送礼意图。价格词统一留在 app.services.price_intent 处理;若把它们当作
    # 检索同义词,会淹没精确的品类匹配结果。
    "送礼": ("礼物", "礼盒", "体面", "高端"),
}


def expansion_terms(text: str, *, max_terms: int = MAX_EXTRA_TERMS) -> list[str]:
    """返回触发词命中 ``text`` 的已审核扩展词列表。"""
    base = (text or "").strip()
    if not base:
        return []
    if any(signal in base for signal in PRICE_INTENT_SIGNALS):
        return []

    base_lower = base.lower()
    out: list[str] = []
    seen = {base_lower}
    for trigger, terms in SYNONYM_TERMS.items():
        if trigger.lower() not in base_lower:
            continue
        for term in terms:
            key = term.lower()
            if key in seen or key in base_lower:
                continue
            seen.add(key)
            out.append(term)
            if len(out) >= max_terms:
                return out
    return out


def expand_query(text: str, *, max_terms: int = MAX_EXTRA_TERMS) -> list[str]:
    """返回用于多查询(multi-query)检索的查询变体。

    第一项始终是原始查询。第二项(如果存在)是一条紧凑的扩展查询,
    用来提升稀疏/稠密召回,同时避免宽泛的通用词涌入检索结果。
    """
    base = (text or "").strip()
    if not base:
        return []

    extras = expansion_terms(base, max_terms=max_terms)
    if not extras:
        return [base]
    return [base, f"{base} {' '.join(extras)}"]
