"""Negation / exclusion extraction.

When a user says "不要日系品牌, 不要含酒精, 除了 iPhone", we need to lift
those constraints out of the prose so we can apply them as filters on the
catalog rather than relying on the LLM to obey them post-hoc.

Returns a structured dict the retrieval layer can consume:

```
{
    "exclude_brands": ["雅诗兰黛", ...],
    "exclude_categories": ["美妆护肤", ...],
    "exclude_keywords": ["酒精", "日本"]
}
```
"""

from __future__ import annotations

import json
import os
import re
import urllib.request


def _local_country_keywords(text: str) -> list[str]:
    """Local-only fallback: scan raw user text for country-trigger keywords
    so `apply_negation` → `excluded_countries` can still work when no
    TokenRouter key is configured (e.g. when LLM_PROVIDER=doubao).

    Returns the matched trigger phrases (e.g. ['日系']) so
    `excluded_countries(...)` resolves them to ISO codes via
    `brand_origin.COUNTRY_KEYWORDS`.
    """
    try:
        from rag.retrieve.brand_origin import COUNTRY_KEYWORDS
    except Exception:
        return []
    found: list[str] = []
    for kws in COUNTRY_KEYWORDS.values():
        for kw in kws:
            if kw in text and kw not in found:
                found.append(kw)
    return found


def extract_negation(text: str) -> dict:
    """Best-effort extraction. Silent fallback to {} on any error
    (the prompt still has a fallback rule, so behavior degrades gracefully)."""
    if not text or not any(neg in text for neg in ("不要", "不含", "不带", "除了", "排除")):
        return {"exclude_brands": [], "exclude_categories": [], "exclude_keywords": []}

    # Always include locally-detected country triggers ("日系"/"美系"/...) so
    # the brand-origin filter works even without an LLM call. The LLM-
    # extracted keywords are union'd into this list when available.
    local_kw = _local_country_keywords(text)

    key = os.getenv("TOKENROUTER_API_KEY")
    if not key:
        return {"exclude_brands": [], "exclude_categories": [], "exclude_keywords": local_kw}

    prompt = (
        "用户在做电商查询。从下面这段话中**只提取**用户明确说不要的内容。\n"
        "字段：exclude_brands（品牌名）、exclude_categories（类目，从 美妆护肤/数码电子/服饰运动/食品生活/母婴健康/家居家具 里选）、exclude_keywords（具体要排除的成分/属性/国别）。\n"
        "返回 JSON 对象，仅 JSON，不要解释。\n\n"
        f"用户说：{text}\n\n"
        "示例：\n"
        '输入：推荐防晒霜，不要含酒精的，也不要日系品牌\n'
        '输出: {"exclude_brands":[],"exclude_categories":[],"exclude_keywords":["酒精","日本","日系"]}\n'
    )
    body = json.dumps({
        "model": os.getenv("TOKENROUTER_MODEL", "claude-haiku-4-5"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```\w*\s*", "", content).rstrip("`").strip()
        result = json.loads(content)
        llm_kw = [str(s).strip() for s in result.get("exclude_keywords", []) if s]
        # Union LLM-extracted + local country triggers, preserving order, dedup.
        merged_kw = list(dict.fromkeys(llm_kw + local_kw))
        return {
            "exclude_brands": [str(s).strip() for s in result.get("exclude_brands", []) if s],
            "exclude_categories": [str(s).strip() for s in result.get("exclude_categories", []) if s],
            "exclude_keywords": merged_kw,
        }
    except Exception:
        # LLM unavailable — fall back to local country detection at minimum.
        return {"exclude_brands": [], "exclude_categories": [], "exclude_keywords": local_kw}


def apply_negation(products: list[dict], neg: dict) -> list[dict]:
    """Filter the candidate list.

    Removes products that match any of:
      1. excluded brand (substring match on brand name),
      2. excluded category,
      3. excluded keyword in title/marketing_description (textual),
      4. **excluded brand-origin country** (Round 7 fix): when the user says
         "不要日系" / "不要美系", resolve each product's origin via
         `provenance.origin_country` or the `brand_origin` lookup table
         and drop products whose origin matches the excluded country.
    """
    if not neg or not (neg.get("exclude_brands") or neg.get("exclude_categories") or neg.get("exclude_keywords")):
        return products

    excl_brands = {b.lower() for b in neg.get("exclude_brands", [])}
    excl_cats = {c for c in neg.get("exclude_categories", [])}
    excl_kw = [k for k in neg.get("exclude_keywords", []) if k]

    # Resolve country exclusions from the keyword set (e.g. "日系" → JP).
    try:
        from rag.retrieve.brand_origin import excluded_countries, product_origin
        excl_countries = excluded_countries(excl_kw)
    except Exception:
        excl_countries = set()
        product_origin = lambda p: None  # type: ignore

    out: list[dict] = []
    for p in products:
        brand = (p.get("brand") or "").lower()
        if any(b and b in brand for b in excl_brands):
            continue
        if p.get("category") in excl_cats:
            continue
        title = p.get("title", "")
        desc = (p.get("rag_knowledge", {}) or {}).get("marketing_description", "")
        haystack = f"{title}\n{desc}".lower()
        if any(k.lower() in haystack for k in excl_kw):
            continue
        if excl_countries:
            origin = product_origin(p)
            if origin and origin in excl_countries:
                continue
        out.append(p)
    return out
