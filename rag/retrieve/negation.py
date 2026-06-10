"""否定 / 排除条件抽取。

当用户说"不要日系品牌, 不要含酒精, 除了 iPhone"时, 需要把这些约束从自然语言
中抽取出来, 直接作为商品目录上的过滤条件来执行, 而不是事后指望 LLM 自觉遵守。

返回一个检索层可直接消费的结构化 dict:

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


# 否定触发词。"不想要/不需要" 排在 "不要" 之前(正则按最左分支优先匹配, 长词在前更清晰)。
# "(?<!有)没有" 是为了让 "有没有X" 这种"是否有货"的正向提问(如 "有没有华为手机")
# 不被误读成"排除 X" —— 只有单独出现的 "没有X" 才算否定。
_NEG_PHRASE_RE = re.compile(
    r"(?:不想要|不需要|不要|别要|别给我|不考虑|不买|不选|除了|不含|不带|不是|排除|也不要|(?<!有)没有)\s*([^,，。;；！!?？]+)"
)


def _local_country_keywords(text: str) -> list[str]:
    """纯本地兜底: 直接在用户原文里扫描国别触发词, 使得在没有配置
    TokenRouter key 时(例如 LLM_PROVIDER=doubao), `apply_negation` →
    `excluded_countries` 这条链路仍然可用。

    返回命中的触发词原文(如 ['日系']), 由 `excluded_countries(...)` 通过
    `brand_origin.COUNTRY_KEYWORDS` 把它们解析成 ISO 国家码。
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


def _local_brand_mentions(text: str) -> list[str]:
    """纯本地兜底: 在否定短语("不要 X" / "除了 X")中扫描显式提到的品牌,
    返回命中的标准品牌名。

    背景: 没有 TokenRouter key 时, 基于 LLM 的 extract_negation 永远填不上
    `exclude_brands`, 导致 "不要 Apple" / "不要 Sony" / "不要 雀巢" 这类查询
    里被点名的品牌又混回了 top-k。这个兜底通过把否定词后面的短语与
    `brand_origin.BRAND_ORIGIN` 已知品牌集求交集来兜住这些 case。

    只在否定语境内(不要/除了/不含 等触发词之后)生效, 所以
    "推荐 Apple iPhone" 这类查询不会误把 Apple 排除掉。
    """
    try:
        from rag.retrieve.brand_origin import BRAND_ORIGIN
    except Exception:
        return []
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    # 已知品牌按长度降序排序, 使 "Apple 苹果" 先于 "Apple" 被匹配到
    known = sorted(BRAND_ORIGIN.keys(), key=len, reverse=True)
    for m in _NEG_PHRASE_RE.finditer(text):
        phrase = m.group(1).lower()
        for brand in known:
            bl = brand.lower()
            if not bl or bl in seen:
                continue
            if bl in phrase:
                found.append(brand)
                seen.add(bl)
    return found


def extract_negation(text: str) -> dict:
    """尽力而为的抽取。任何报错都静默回退到空结果
    (prompt 里仍保留兜底规则, 所以整体行为是优雅降级的)。"""
    if not text or not any(neg in text for neg in (
        "不要", "别要", "别给我", "不想要", "不需要", "不考虑", "不买", "不选",
        "不含", "不带", "除了", "排除", "就算了", "就不看", "不用了"
    )):
        return {"exclude_brands": [], "exclude_categories": [], "exclude_keywords": []}

    # 始终带上本地检测到的国别触发词("日系"/"美系"/...), 这样即使不调 LLM,
    # 品牌产地(brand-origin)过滤也能正常工作。LLM 抽取到的关键词可用时
    # 会再并(union)进这个列表。
    local_kw = _local_country_keywords(text)
    local_brands = _local_brand_mentions(text)

    key = os.getenv("TOKENROUTER_API_KEY")
    if not key:
        return {
            "exclude_brands": local_brands,
            "exclude_categories": [],
            "exclude_keywords": local_kw,
        }

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
        llm_brands = [str(s).strip() for s in result.get("exclude_brands", []) if s]
        llm_kw = [str(s).strip() for s in result.get("exclude_keywords", []) if s]
        # 合并 LLM 抽取结果与本地检测结果, 保持顺序并去重。
        merged_brands = list(dict.fromkeys(llm_brands + local_brands))
        merged_kw = list(dict.fromkeys(llm_kw + local_kw))
        return {
            "exclude_brands": merged_brands,
            "exclude_categories": [str(s).strip() for s in result.get("exclude_categories", []) if s],
            "exclude_keywords": merged_kw,
        }
    except Exception:
        # LLM 不可用 —— 至少回退到本地检测结果。
        return {
            "exclude_brands": local_brands,
            "exclude_categories": [],
            "exclude_keywords": local_kw,
        }


def apply_negation(products: list[dict], neg: dict) -> list[dict]:
    """过滤候选商品列表。

    命中以下任一条件的商品会被剔除:
      1. 被排除的品牌(对品牌名做子串匹配),
      2. 被排除的类目,
      3. 标题/marketing_description 文本中含有被排除的关键词,
      4. **被排除的品牌产地国别**(Round 7 修复): 当用户说
         "不要日系" / "不要美系" 时, 通过 `provenance.origin_country`
         或 `brand_origin` 查找表解析每件商品的产地,
         产地命中被排除国家的商品会被丢弃。
    """
    if not neg or not (neg.get("exclude_brands") or neg.get("exclude_categories") or neg.get("exclude_keywords")):
        return products

    # 把每个被排除的品牌扩展到其已知别名, 使 "不要 Nike" 也能命中
    # 中文标注的 "耐克" 商品(反之亦然)。
    try:
        from rag.retrieve.brand_origin import expand_brand_aliases
        excl_brands: set[str] = set()
        for b in neg.get("exclude_brands", []) or []:
            if not b:
                continue
            excl_brands |= expand_brand_aliases(b)
    except Exception:
        excl_brands = {b.lower() for b in neg.get("exclude_brands", [])}
    excl_cats = {c for c in neg.get("exclude_categories", [])}
    excl_kw = [k for k in neg.get("exclude_keywords", []) if k]

    # 从关键词集合解析国别排除条件(如 "日系" → JP)。
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


# ---------------------------------------------------------------------------
# R11.fix —— 正向产地约束("要国产 / 国货")。区别于上面的否定抽取器
# (它只处理 不要X / 除了X)。实现为独立的过滤器, 以保证不会让
# 显式否定那条路径出现回归。
# ---------------------------------------------------------------------------

_DOMESTIC_RE = re.compile(r"国产|国货|国内品牌|本土品牌|民族品牌")
_DOMESTIC_NEG_RE = re.compile(r"不要\s*(?:国产|国货|国内品牌)|别要\s*(?:国产|国货)|非国产")


def requires_domestic(text: str) -> bool:
    """当用户**正向**要求 国产 / 国货(CN 产地)商品时返回 True ——
    这是一个应当剔除外国品牌的产地约束。同时防住否定形式 '不要国产'
    被误判为正向需求。"""
    if not text:
        return False
    return bool(_DOMESTIC_RE.search(text)) and not _DOMESTIC_NEG_RE.search(text)


def apply_domestic_filter(products: list[dict]) -> list[dict]:
    """只保留 CN 产地(或产地未知)的商品, 剔除已知外国品牌。
    使用 brand_origin.product_origin —— 它对 AI 生成的演示种子数据按**品牌名**
    解析产地(这批数据的 provenance.origin_country 被错标成了 'CN'),
    因此 adidas / HOKA / 迪卡侬 这类外国品牌能被正确剔除。失败软着陆(fail-soft):
    若过滤会把列表清空, 则原样返回输入。"""
    try:
        from rag.retrieve.brand_origin import product_origin
    except Exception:
        return products
    out = [p for p in products if (product_origin(p) or "CN") == "CN"]
    return out if out else products


# "X 以外 / X 之外" —— 排除品牌 X(例如多轮追问 "华为以外还有吗")。
_EXCEPT_RE = re.compile(r"([^，。、；;！!？?\s]{2,16})\s*(?:以外|之外)")


def except_brands(text: str) -> list[str]:
    """用户通过 'X以外 / X之外' 句式要求**排除**的品牌(例如多轮追问
    '华为以外还有吗')。以外/之外 前面的短语会与已知品牌集求交集,
    所以 '五百元以外' 这种非品牌短语不会排除任何东西。
    独立实现 —— 不触碰 不要X/除了X 那条路径。"""
    if not text:
        return []
    try:
        from rag.retrieve.brand_origin import BRAND_ORIGIN
    except Exception:
        return []
    known = sorted(BRAND_ORIGIN.keys(), key=len, reverse=True)
    found: list[str] = []
    seen: set[str] = set()
    for m in _EXCEPT_RE.finditer(text):
        phrase = m.group(1).lower()
        for b in known:
            bl = b.lower()
            if bl and bl not in seen and bl in phrase:
                found.append(b)
                seen.add(bl)
    return found
