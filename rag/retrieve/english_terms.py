"""English shopping-term → Chinese category hints, shared by retrieval layers.

The catalog (and every keyword table derived from it) is Chinese. An English
query used to get Chinese hints only inside chat.py's query augmenter, so the
HARD-FILTER inference (rag.retrieve.constraints) saw the raw English text,
extracted no category/sub_category, and the price-only WHERE returned cheap
unrelated products ("noise cancelling headphones under 1000" → yogurt cards).

Single source of truth for the mapping lives here so the query augmenter
(server/app/routes/chat.py) and the constraint parser (constraints.py) can't
drift apart again.
"""

from __future__ import annotations

import re

# English term → Chinese category word. Matched with a LEADING word boundary
# so 'phone' doesn't fire inside 'headphones'/'earphones'/'iphone' (only the
# longer term does), while 'spf50' still triggers 'spf'. Longer/more specific
# terms should precede generic ones so the specific Chinese hint lands first.
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
    ("shoes", "鞋子"), ("backpack", "双肩包"), ("snacks", "零食"),
    ("snack", "零食"), ("coffee", "咖啡"), ("diaper", "纸尿裤"),
    ("toothpaste", "牙膏"), ("phone", "手机"),
)

_CJK_RE = re.compile(r"[一-鿿]")


def looks_english(text: str) -> bool:
    """True when the text is unambiguously English: no CJK characters and at
    least a few ASCII letters. Used to pick the reply language and to gate the
    English-only matching below."""
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
    """Chinese category hint words for the English shopping terms in ``text``.

    Empty when the text already contains Chinese — a mixed query carries its
    own Chinese signals and augmenting it would skew the candidate pool.
    """
    if not text or _CJK_RE.search(text):
        return []
    low = text.lower()
    if not any("a" <= c <= "z" for c in low):
        return []
    return _term_hits(low)


def augment_english_query(query: str, extra_text: str = "") -> str:
    """Append Chinese category words for any English shopping term present.

    Terms are looked up in both ``query`` and ``extra_text`` (the raw user
    message — the rewritten retrieval query may have dropped a term), but only
    a no-Chinese ``query`` is augmented. AUGMENTS, never replaces.
    """
    if not query or _CJK_RE.search(query):
        return query
    if not any("a" <= c <= "z" for c in query.lower()):
        return query
    hits = [zh for zh in _term_hits(f"{query} {extra_text}".lower()) if zh not in query]
    return f"{query} {' '.join(hits)}".strip() if hits else query
