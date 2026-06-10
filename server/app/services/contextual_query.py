"""基于多轮对话历史构造适合检索的查询串。

LLM 看到的仍然是用户的原始消息。本模块只改写发给检索侧的字符串,
这样像"再便宜点的呢"这种简短追问就能继承上一轮的购物需求,
而不是作为一个脱离上下文的碎片直接做 embedding。
"""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas.chat import ChatMessage


_CHEAP = ("便宜", "平价", "性价比", "预算", "低价", "划算")
_EXPENSIVE = ("贵", "高端", "旗舰", "预算充足", "好一点")
_ALTERNATIVE = ("换", "其他", "别的", "还有", "类似", "同款")
_NEGATION = ("不要", "除了", "不含", "不带", "排除")
# 纯对比类追问("对比一下哪个好" / "哪个更好" / "有什么区别")没有点名任何商品,
# 必须继承上一轮的品类——否则检索拿到的就是一句脱离上下文的"对比一下哪个好",
# LLM 也没有东西可对比。
_COMPARE = ("对比", "哪个", "哪款", "比一", "比较", "区别", "谁好", "更好")
_FOLLOWUP_HINTS = _CHEAP + _EXPENSIVE + _ALTERNATIVE + _NEGATION + _COMPARE + ("这个", "那个", "它", "再")

# R13 — 纯约束(CONSTRAINT)类追问同样没有点名商品。只有预算("1000以内")、
# 只有属性("要降噪的" / "休闲款1000以内")或只有品牌("苹果的呢")的轮次,
# 必须继承上一轮的品类,而不能作为脱离上下文的碎片直接进检索
# (此前"1000以内"就因此检索出了 T恤)。
_PRICE_CONSTRAINT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|块|rmb|￥|¥)?\s*(?:以内|以下|以上|之内|内|封顶|左右|上下|起步|起)"
    r"|(?:不要?超过|预算|价格)\s*\d+(?:\.\d+)?\s*(?:元|块)?",
    re.IGNORECASE,
)
# 简短的细化句式: "要降噪的" / "苹果的呢" / "休闲款" / "运动版"。
_REFINEMENT_RE = re.compile(
    r"^(?:要|想要|最好|有没有|能不能|换成?|来个?)?[^，。,;；]{1,8}(?:的|款|色|系列|版)(?:呢|吗|呗|吧|的)?$"
)
# 英文追问("any cheaper ones?" / "what about apple" / "under 800")。
_EN_FOLLOWUP_HINTS = (
    "cheap", "expensive", "pricier", "premium", "budget", "afford",
    "other", "else", "another", "alternative", "similar", "instead",
    "option", "under", "below", "within", "less than", "more than",
    "what about", "how about", "compare", "which one", "better",
)

_ANCHOR_TERMS = (
    "洗面奶", "洁面", "精华", "防晒", "护肤", "面霜", "乳液", "手机", "耳机", "笔记本",
    "电脑", "平板", "相机", "跑鞋", "鞋", "书", "小说", "食品", "零食", "婴儿", "母婴",
    "家具", "椅", "桌", "帐篷", "户外",
)

_QUESTION_FILLER = re.compile(r"[吗呢啊呀～~？?。！!，, ]+")


def message_text(message: ChatMessage) -> str:
    """从纯文本或多模态聊天消息中提取文本。"""
    content = message.content
    if isinstance(content, str):
        return content.strip()

    parts: list[str] = []
    for part in content:
        if getattr(part, "type", None) == "text":
            text = getattr(part, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


# "不要X的Y"(X = 品牌/属性, Y = 品类)——否定词在句首。贪婪的否定提取器会把
# 正向目标 Y 一并扫进排除集,导致检索把整个品类都过滤掉、返回空结果
# ("不要苹果的耳机" → 0 张卡片)。改写成 "Y 不要X" 把正向目标放到句首
# (即已经能正常工作的句式, 如 "耳机不要苹果"), 这样被排除的就只剩 X。
_NEG_OBJ_RE = re.compile(r"^(不要|别要|别给我|不想要|不需要|不考虑|不买|不选)([^的，。,；;\s]{1,12})的([^，。,；;]{1,16})$")


def _reorder_negation_object(text: str) -> str:
    m = _NEG_OBJ_RE.match(text.strip())
    return f"{m.group(3)} {m.group(1)}{m.group(2)}" if m else text


def build_retrieval_query(messages: Iterable[ChatMessage], *, fallback: str = "拍照找货") -> str:
    """返回要发送给 RAG 的查询字符串。

    如果用户最新一轮是简短追问, 就在前面拼上最近一条完整的用户请求;
    否则原样返回用户最新一轮的文本。
    """
    messages = list(messages)
    latest_user_index: int | None = None
    current = ""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            latest_user_index = i
            current = message_text(messages[i])
            break

    if latest_user_index is None:
        return fallback

    current = current.strip()
    if not current or current == "(image-only query)":
        return fallback

    # "不要X的Y" → "Y 不要X", 让正向目标作为检索锚点, 只排除 X
    # (修复 "不要苹果的耳机" 返回 0 张卡片的问题)。
    current = _reorder_negation_object(current)

    if not _looks_like_followup(current):
        return current

    previous_user_texts: list[str] = []
    for m in messages[:latest_user_index]:
        if m.role != "user":
            continue
        text = message_text(m)
        if text:
            previous_user_texts.append(text)

    anchor = _nearest_anchor(previous_user_texts)
    if not anchor:
        return current or fallback

    return _compose_contextual_query(anchor, current)


def _carries_category_signal(text: str) -> bool:
    """该轮自带商品品类/子品类时返回 True——这样的轮次是自洽的,
    不属于必须继承上下文的裸约束。
    (英文词同样算数: constraints 通过 english_terms 完成映射。)"""
    try:
        from rag.retrieve.constraints import _category, _sub_categories

        return bool(_category(text) or _sub_categories(text))
    except Exception:
        return False


def _looks_like_followup(text: str) -> bool:
    compact = _QUESTION_FILLER.sub("", text)
    if not compact:
        return False
    if len(compact) <= 8 and any(h in compact for h in _FOLLOWUP_HINTS):
        return True
    if len(compact) <= 12 and any(p in compact for p in ("这个", "那个", "它", "再", "换一个")):
        return True
    # R13 — 价格方向类抱怨/细化追问("这些太便宜了，
    # 推荐一些贵的" / "有没有更高端的")没有自带品类, 必须继承上一轮的品类。
    # 否则这句光秃秃的价格抱怨会作为脱离上下文的查询去做 embedding,
    # 相关性闸门会什么都不返回("推荐不出来")。
    if (len(compact) <= 20
            and any(d in compact for d in _CHEAP + _EXPENSIVE)
            and not any(t in compact for t in _ANCHOR_TERMS)
            and not _carries_category_signal(text)):
        return True
    # R13 — 纯约束轮次。每个分支都要求该轮自身不携带任何品类
    # ("1000以内的耳机" 保持自洽; "1000以内"、
    # "休闲款1000以内"、"要降噪的"、"苹果的呢" 走继承)。
    residue = _PRICE_CONSTRAINT_RE.sub("", compact)
    if residue != compact and len(residue) <= 6 and not _carries_category_signal(compact):
        return True
    if len(compact) <= 10 and _REFINEMENT_RE.match(compact) and not _carries_category_signal(compact):
        return True
    # 英文简短追问("any cheaper ones?")。在原始文本上做按词边界的匹配;
    # 自带英文品类的轮次("cheaper headphones")是自洽的,
    # 可以独立作为检索锚点。
    low = text.lower().strip()
    words = re.findall(r"[a-z']+", low)
    if (
        words
        and len(words) <= 7
        and not any("一" <= c <= "鿿" for c in low)
        and any(h in low for h in _EN_FOLLOWUP_HINTS)
        and not _carries_category_signal(low)
    ):
        return True
    return False


def _nearest_anchor(previous_user_texts: list[str]) -> str | None:
    for text in reversed(previous_user_texts):
        compact = _QUESTION_FILLER.sub("", text)
        if not compact:
            continue
        # 注意: 传入原始文本而不是 `compact`——去掉空格会破坏
        # 追问/品类检查所依赖的英文单词边界。
        if (
            _looks_like_followup(text)
            and not any(t in compact for t in _ANCHOR_TERMS)
            and not _carries_category_signal(text)
        ):
            continue
        return text.strip()
    return None


def _compose_contextual_query(anchor: str, current: str) -> str:
    expansions: list[str] = []
    if any(t in current for t in _CHEAP):
        expansions.extend(["更便宜", "平价", "低价", "性价比"])
    if any(t in current for t in _EXPENSIVE):
        expansions.extend(["更高端", "高价位", "旗舰"])
    if any(t in current for t in _ALTERNATIVE):
        expansions.extend(["替代推荐", "相似商品"])

    pieces = [anchor]
    if expansions:
        pieces.append(" ".join(dict.fromkeys(expansions)))
    pieces.append(current)
    return " ".join(p for p in pieces if p).strip()
