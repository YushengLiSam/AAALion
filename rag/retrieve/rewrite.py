"""LLM-based query rewriting.

Vague queries like "便宜点的" or "好用的" are hard for dense embeddings to
disambiguate. This module asks the LLM to expand the query into 1-2
alternative phrasings BEFORE retrieval, so we can run multi-query and
fuse results.

Cost-aware: skipped when the query already contains specifics (a brand,
a price range, a category) — those embed well on their own.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request


_SPECIFIC_HINTS = (
    "元", "块", "¥", "iPhone", "Apple", "华为", "小米", "美妆", "数码", "食品", "服饰",
    "雅诗兰黛", "兰蔻", "理肤泉", "薇诺娜", "防晒", "洗面奶", "精华", "笔记本",
)


def _looks_specific(text: str) -> bool:
    """If the query contains a concrete brand / category / price hint, skip rewriting."""
    return any(h in text for h in _SPECIFIC_HINTS)


def rewrite_query(text: str, max_extras: int = 2) -> list[str]:
    """Return the original query + up to max_extras alternative phrasings.

    Quiet failure: on any error, returns just the original. Worst case is
    we run pure dense retrieval — no harder behavior break.
    """
    text = (text or "").strip()
    if not text or _looks_specific(text):
        return [text]
    key = os.getenv("TOKENROUTER_API_KEY")
    if not key:
        return [text]

    prompt = (
        "用户用中文查询电商商品。给我两个**不同角度**的改写，便于向量检索。"
        "返回 JSON 数组，每条 ≤ 30 字，仅 JSON，不要解释。\n"
        f"原始查询：{text}\n\n"
        "示例：\n"
        "  原始：便宜点的\n"
        '  返回: ["性价比高 平价 推荐", "预算友好 高质优价"]\n'
    )
    body = json.dumps({
        "model": os.getenv("TOKENROUTER_MODEL", "claude-haiku-4-5"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.3,
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
        content = data["choices"][0]["message"]["content"]
        # Strip code fences just in case
        content = re.sub(r"^```\w*\s*", "", content.strip()).rstrip("`")
        arr = json.loads(content)
        if isinstance(arr, list):
            extras = [str(s).strip() for s in arr if isinstance(s, str)][:max_extras]
            return [text] + [e for e in extras if e and e != text]
    except Exception:
        pass
    return [text]
