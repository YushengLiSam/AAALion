"""Brand → origin-country lookup for the negation filter.

Round 7 fix: `apply_negation` used to filter by literal brand string and
title/description keyword. "不要日系" failed to exclude 安热沙 because the
title contains neither 日本 nor 日系. This table lets the filter resolve a
product's origin from its brand (when `provenance.origin_country` is absent
on AI-gen seed entries) and drop it when the user excludes that country.

Coverage: the ~30 named brands in our current catalog. New brands fall
through to "unknown" — same behavior as pre-Round-7 (no regression).
Extend the dict as the catalog grows. Tujie or Sam can also add to it
without touching `negation.py`.
"""

from __future__ import annotations

# ISO 3166-1 alpha-2 country codes. Keep keys lowercase-stripped for matching.
BRAND_ORIGIN: dict[str, str] = {
    # Japanese 日系 (the case that prompted this fix).
    "安热沙": "JP",
    "anessa": "JP",
    "资生堂": "JP",
    "shiseido": "JP",
    "sk-ii": "JP",
    "sk2": "JP",
    "shu uemura": "JP",
    "kiehl's": "US",
    "kiehls": "US",
    "tatcha": "JP",
    "muji": "JP",
    "kewpie": "JP",
    "pigeon": "JP",
    "gerber": "JP",  # owned by Nestlé but original Japanese Gerber co-brand in CN catalog
    "nintendo": "JP",
    "sony": "JP",
    "dji": "CN",
    "calpico": "JP",
    "calpis": "JP",
    "glico": "JP",
    "kit kat": "JP",
    "nestlé": "JP",
    "nestle": "JP",
    "uniqlo": "JP",
    # American 美系
    "雅诗兰黛": "US",
    "estée lauder": "US",
    "estee lauder": "US",
    "apple": "US",
    "bose": "US",
    "the north face": "US",
    "patagonia": "US",
    "trader joe's": "US",
    "trader joes": "US",
    "levi's": "US",
    "levis": "US",
    "the ordinary": "US",
    "kiehl's since 1851": "US",
    # French 法系
    "理肤泉": "FR",
    "la roche-posay": "FR",
    "la roche posay": "FR",
    "兰蔻": "FR",
    "lancôme": "FR",
    "lancome": "FR",
    "巴黎欧莱雅": "FR",
    "l'oréal paris": "FR",
    "loreal paris": "FR",
    "loreal": "FR",
    # Korean 韩系
    "雪花秀": "KR",
    "sulwhasoo": "KR",
    "innisfree": "KR",
    "悦诗风吟": "KR",
    # German 德系
    "adidas": "DE",
    "puma": "DE",
    # British
    "the body shop": "GB",
    # Italian
    "ferrari": "IT",
    # Chinese 国货
    "薇诺娜": "CN",
    "winona": "CN",
    "珀莱雅": "CN",
    "proya": "CN",
    "花西子": "CN",
    "完美日记": "CN",
    "perfect diary": "CN",
    "华为": "CN",
    "huawei": "CN",
    "小米": "CN",
    "xiaomi": "CN",
    "公牛": "CN",
    "bull": "CN",
    "飞鹤": "CN",
    "firmus": "CN",
    "汤臣倍健": "CN",
    "by-health": "CN",
    "斯利安": "CN",
    "海尔": "CN",
    "haier": "CN",
    "迪卡侬": "FR",  # French brand, very popular in CN — flag as FR for honesty
    "decathlon": "FR",
    "凯乐石": "CN",
    "kailas": "CN",
    "牧高笛": "CN",
    "mobigarden": "CN",
    "探路者": "CN",
    "toread": "CN",
    "网易严选": "CN",
    "雅芳婷": "CN",
    "珊珂": "JP",  # 珊珂 (Senka) is Shiseido Japan
    "senka": "JP",
    "百雀羚": "CN",
    "自然堂": "CN",
    "韩束": "CN",
    "丸美": "CN",
    "帮宝适": "US",  # Pampers (Procter & Gamble US)
    "pampers": "US",
    "好奇": "US",  # Huggies (Kimberly-Clark US)
    "huggies": "US",
    "嘉宝": "US",  # Gerber US
    "重庆出版社": "CN",
    "北京十月文艺出版社": "CN",
    "商务印书馆": "CN",
    "吉林美术出版社": "CN",
}

# Country code → trigger keywords users might type when excluding.
# Match is case-insensitive and substring-based; small set keeps it precise.
COUNTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "JP": ("日本", "日系", "日货", "倭"),
    "US": ("美国", "美系", "美货"),
    "KR": ("韩国", "韩系", "韩货"),
    "FR": ("法国", "法系"),
    "DE": ("德国", "德系"),
    "GB": ("英国", "英系"),
    "IT": ("意大利", "意系"),
    "CN": ("国货", "中国"),  # rarely excluded but supported for completeness
}


def lookup_origin(brand: str) -> str | None:
    """Return the ISO-2 country code for a brand, or None if unknown.

    Matching is case-insensitive and trims whitespace. Substring match in
    both directions to catch "雅诗兰黛 / Estée Lauder" hybrid titles.
    """
    if not brand:
        return None
    key = brand.strip().lower()
    if key in BRAND_ORIGIN:
        return BRAND_ORIGIN[key]
    # Loose match: any known brand name that's a substring of the input,
    # or vice-versa. Keeps the dictionary compact.
    for known_brand, country in BRAND_ORIGIN.items():
        if known_brand in key or key in known_brand:
            return country
    return None


def excluded_countries(exclude_keywords: list[str]) -> set[str]:
    """Given the negation extractor's `exclude_keywords`, return the set of
    ISO-2 country codes the user wants to exclude."""
    excluded: set[str] = set()
    if not exclude_keywords:
        return excluded
    haystack = " ".join(k.lower() for k in exclude_keywords if k)
    for country, kws in COUNTRY_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            excluded.add(country)
    return excluded


def product_origin(product: dict) -> str | None:
    """Resolve a product's origin country. Prefers explicit provenance
    metadata (Round 6 real products carry this) and falls back to the
    brand-name lookup."""
    prov = product.get("provenance") or {}
    explicit = (prov.get("origin_country") or "").strip().upper()
    if explicit and len(explicit) == 2:
        # Treat AI-gen demo seed (`source_platform = "AI-gen (demo)"`) as
        # unknown origin — its `origin_country` default is "CN" but we don't
        # want to falsely tag a 雅诗兰黛 (US) AI-gen card as CN. Brand-name
        # lookup is more reliable for AI-gen entries.
        if prov.get("source_platform") == "AI-gen (demo)":
            from_brand = lookup_origin(product.get("brand") or "")
            return from_brand or explicit
        return explicit
    return lookup_origin(product.get("brand") or "")
