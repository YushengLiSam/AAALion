"""Build a retrieval-friendly query from multi-turn chat history.

The LLM should still see the user's original message. This module only
rewrites the string sent to retrieval, so short follow-ups like "再便宜点的呢"
inherit the previous shopping need instead of embedding as a context-free
fragment.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas.chat import ChatMessage


_CHEAP = ("便宜", "平价", "性价比", "预算", "低价", "划算")
_EXPENSIVE = ("贵", "高端", "旗舰", "预算充足", "好一点")
_ALTERNATIVE = ("换", "其他", "别的", "还有", "类似", "同款")
_NEGATION = ("不要", "除了", "不含", "不带", "排除")
_FOLLOWUP_HINTS = _CHEAP + _EXPENSIVE + _ALTERNATIVE + _NEGATION + ("这个", "那个", "它", "再")

_ANCHOR_TERMS = (
    "洗面奶", "洁面", "精华", "防晒", "护肤", "面霜", "乳液", "手机", "耳机", "笔记本",
    "电脑", "平板", "相机", "跑鞋", "鞋", "书", "小说", "食品", "零食", "婴儿", "母婴",
    "家具", "椅", "桌", "帐篷", "户外",
)

_QUESTION_FILLER = re.compile(r"[吗呢啊呀～~？?。！!，, ]+")


def message_text(message: ChatMessage) -> str:
    """Extract text from a plain or multimodal chat message."""
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


def build_retrieval_query(messages: Iterable[ChatMessage], *, fallback: str = "拍照找货") -> str:
    """Return the query string to send to RAG.

    If the latest user turn is a short follow-up, prepend the nearest prior
    full user request. Otherwise return the latest user text unchanged.
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


def _looks_like_followup(text: str) -> bool:
    compact = _QUESTION_FILLER.sub("", text)
    if not compact:
        return False
    if len(compact) <= 8 and any(h in compact for h in _FOLLOWUP_HINTS):
        return True
    if len(compact) <= 12 and any(p in compact for p in ("这个", "那个", "它", "再", "换一个")):
        return True
    return False


def _nearest_anchor(previous_user_texts: list[str]) -> str | None:
    for text in reversed(previous_user_texts):
        compact = _QUESTION_FILLER.sub("", text)
        if not compact:
            continue
        if _looks_like_followup(compact) and not any(t in compact for t in _ANCHOR_TERMS):
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
