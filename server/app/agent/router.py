"""智能体路由:纯规则,不调 LLM。

走智能体(复杂多步):
  multihop        依赖另一件商品的问法(比 X 便宜 / 跟 X 同价位 / 同品牌 / 买了 X 配 Y),
                  直接复用 rag.retrieve.multihop.detect_multihop(锚点必须能落到目录);
  comparison      对比意图 + 点名 ≥2 个不同品牌 / 产品线;
  bundle          "N 元(内)配一套 / 配齐 / 搭配 …"这类预算配套;
  cross_currency  显式跨币种比较(美元 / 海外版 / 直邮 … 同时带比较词)。
其余一律走快路——包括"这两款哪个好"(没点名,快路的对比附加段已经够用)。

保守取向:宁可漏判走快路(行为与今天完全一致),也不误判把简单问题送进
多 4-6 秒的智能体路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 对比意图:与 chat._COMPARISON_INTENT / rag_client._COMPARISON_RE 同口径的并集
_COMPARE_RE = re.compile(
    r"对比|对照|哪个|哪款|vs\.?|相比|比一?比|比较|区别|差别|怎么选|选哪|还是.{0,12}[?？吗呢]|[和跟与][^，。,；;]{1,16}比",
    re.IGNORECASE,
)
# 预算配套:"3000元配一套跑步装备" / "预算2000配齐露营装备" / "5000以内搭配一套通勤穿搭"
_BUNDLE_RE = re.compile(
    r"(?P<amt>\d+(?:\.\d+)?)\s*(?P<unit>万|w|千|k)?\s*(?:元|块|rmb|¥|￥)?\s*"
    r"(?:以内|以下|之内|内|左右|预算)?[^，。,;；]{0,10}?"
    r"(?:配一套|配齐|配一身|搭一套|搭配一套|搭配|一整套|一套|全套|套装)",
    re.IGNORECASE,
)
_BUNDLE_RE_REV = re.compile(
    r"(?:配一套|配齐|搭一套|搭配一套|一整套|一套|全套)[^，。,;；]{0,16}?"
    r"(?:预算|总共|总价|一共|不超过|以内)?\s*(?P<amt>\d+(?:\.\d+)?)\s*(?P<unit>万|w|千|k)?\s*(?:元|块|rmb)",
    re.IGNORECASE,
)
_FX_RE = re.compile(r"美元|美金|usd|\$|日元|日币|欧元|英镑|港币|港元|海外版|美版|港版|日版|直邮|海淘|外币|汇率",
                    re.IGNORECASE)
_FX_COMPARE_RE = re.compile(r"比|对比|便宜|贵|划算|哪个|差价|相比|换算")

# 产品线(计为独立的点名实体;与 rag_client._PRODUCT_LINE_ANCHORS 对齐并补充常见线)
_PRODUCT_LINES = (
    "iphone", "ipad", "macbook", "airpods", "apple watch", "freebuds", "matebook", "matepad",
    "mate", "pura", "thinkpad", "thinkbook", "xm5", "wh-1000", "qc ultra", "switch",
    "pegasus", "clifton", "ultraboost", "160x", "小黑瓶", "小棕瓶", "神仙水", "红腰子",
)


@dataclass(frozen=True)
class RouteDecision:
    use_agent: bool
    reason: str = "fast"
    bundle_budget_cny: float | None = None


def _amount(m: re.Match) -> float:
    v = float(m.group("amt"))
    unit = (m.group("unit") or "").lower()
    if unit in ("万", "w"):
        v *= 10000
    elif unit in ("千", "k"):
        v *= 1000
    return v


def bundle_budget(text: str) -> float | None:
    """预算配套的总预算(人民币);不是配套问法返回 None。"""
    if not text:
        return None
    for rx in (_BUNDLE_RE, _BUNDLE_RE_REV):
        m = rx.search(text)
        if m:
            v = _amount(m)
            if v >= 10:          # "1套" "2件" 之类的数字不是预算
                return v
    return None


def named_entities(text: str) -> list[str]:
    """点名的品牌 / 产品线(同一品牌的别名算一个)。"""
    lowered = (text or "").casefold()
    groups: list[set[str]] = []
    labels: list[str] = []
    try:
        from rag.retrieve.constraints import _brands
        from app.services.rag_client import _brand_match_terms

        for b in _brands(text)[0] + _brands(text)[1]:
            terms = _brand_match_terms(b)
            if any(g & terms for g in groups):
                continue
            groups.append(set(terms))
            labels.append(b)
    except Exception:
        pass
    for line in _PRODUCT_LINES:
        if line in lowered and line not in labels:
            # 产品线所属品牌已经计过时不重复计(iPhone 与 Apple 是同一个实体)
            try:
                from rag.retrieve.constraints import _brands
                from app.services.rag_client import _brand_match_terms

                owner = _brands(line)[0]
                if owner and any(g & _brand_match_terms(owner[0]) for g in groups):
                    continue
            except Exception:
                pass
            labels.append(line)
            groups.append({line})
    return labels


def should_use_agent(text: str, history=None, *, has_history_cards: bool = False) -> RouteDecision:
    """规则路由。`history` 目前只用于判断是否有上一轮商品卡(会话锚点)。"""
    t = (text or "").strip()
    if not t:
        return RouteDecision(False, "empty")

    # 1) 多跳:锚点必须能落到目录(detect_multihop 内部保证)
    try:
        from rag.retrieve.multihop import detect_multihop

        if detect_multihop(t, has_history_cards=has_history_cards) is not None:
            return RouteDecision(True, "multihop")
    except Exception:
        pass

    # 2) 预算配套
    budget = bundle_budget(t)
    if budget is not None:
        return RouteDecision(True, "bundle", bundle_budget_cny=budget)

    entities = named_entities(t)
    # 3) 显式跨币种比较
    if _FX_RE.search(t) and _FX_COMPARE_RE.search(t) and entities:
        return RouteDecision(True, "cross_currency")

    # 4) 点名 ≥2 个实体的对比
    if _COMPARE_RE.search(t) and len(entities) >= 2:
        return RouteDecision(True, "comparison")

    return RouteDecision(False, "fast")
