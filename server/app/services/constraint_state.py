"""Resolve retrieval filters across a multi-turn shopping conversation.

The query rewriter provides semantic context; this module owns structured
conditions that must persist or be deliberately changed between turns.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from app.schemas.chat import ChatMessage
from app.services.contextual_query import message_text
from rag.retrieve.constraints import build_retrieval_filter
from rag.retrieve.query import Filter


_CLEAR_BUDGET_RE = re.compile(r"(?:预算|价格)(?:不限|不限制)|不限预算|取消预算|取消价格限制")
_CLEAR_BRAND_RE = re.compile(r"(?:品牌不限|不限品牌|取消品牌限制|不限制品牌)")
_CLEAR_CATEGORY_RE = re.compile(r"(?:品类不限|类别不限|不限品类|取消品类限制)")
_ALLOW_BRAND_RE = re.compile(r"(?:也可以|也行|可以接受|不排除|可以考虑)")


def build_conversation_filter(
    messages: Iterable[ChatMessage],
    explicit: Mapping[str, Any] | None = None,
) -> Filter:
    """Return the authoritative retrieval filter for the current turn.

    Empty ``Filter`` is meaningful: it records that prior constraints were
    cancelled, so callers must not re-infer stale constraints from a rewritten
    query that still contains the original anchor text.
    """
    state = Filter()
    for message in messages:
        if message.role != "user":
            continue
        text = message_text(message)
        if text:
            _merge_turn(state, text)

    _apply_explicit(state, explicit)
    return state


def _merge_turn(state: Filter, text: str) -> None:
    clear_budget = bool(_CLEAR_BUDGET_RE.search(text))
    clear_brand = bool(_CLEAR_BRAND_RE.search(text))
    clear_category = bool(_CLEAR_CATEGORY_RE.search(text))
    allow_brand = bool(_ALLOW_BRAND_RE.search(text))

    if clear_budget:
        state.price_min_cny = None
        state.price_max_cny = None
    if clear_brand:
        state.brand_include = None
        state.brand_exclude = None
    if clear_category:
        state.category = None
        state.sub_category = None
        state.sub_categories = None

    turn = build_retrieval_filter(text)
    if turn is None:
        return

    if not clear_category and turn.category:
        category_changed = bool(state.category and state.category != turn.category)
        state.category = turn.category
        if category_changed and not turn.sub_category and not turn.sub_categories:
            state.sub_category = None
            state.sub_categories = None
    if not clear_category and (turn.sub_category or turn.sub_categories):
        state.sub_category = turn.sub_category
        state.sub_categories = list(turn.sub_categories) if turn.sub_categories else None

    if not clear_budget:
        if turn.price_min_cny is not None:
            state.price_min_cny = turn.price_min_cny
        if turn.price_max_cny is not None:
            state.price_max_cny = turn.price_max_cny

    if not clear_brand and turn.brand_exclude:
        state.brand_exclude = _ordered_union(state.brand_exclude, turn.brand_exclude)
        state.brand_include = _remove_brands(state.brand_include, turn.brand_exclude)

    if turn.brand_include:
        if allow_brand:
            state.brand_exclude = _remove_brands(state.brand_exclude, turn.brand_include)
        elif not clear_brand:
            state.brand_include = list(turn.brand_include)
            state.brand_exclude = _remove_brands(state.brand_exclude, turn.brand_include)


def _apply_explicit(state: Filter, explicit: Mapping[str, Any] | None) -> None:
    if not explicit:
        return
    if explicit.get("category") is not None:
        category = str(explicit["category"])
        if state.category != category and explicit.get("sub_category") is None:
            state.sub_category = None
            state.sub_categories = None
        state.category = category
    if explicit.get("sub_category") is not None:
        state.sub_category = str(explicit["sub_category"])
        state.sub_categories = None
    if explicit.get("include_brands") is not None:
        state.brand_include = list(explicit["include_brands"]) or None
        state.brand_exclude = _remove_brands(state.brand_exclude, state.brand_include or [])
    if explicit.get("exclude_brands") is not None:
        state.brand_exclude = list(explicit["exclude_brands"]) or None
        state.brand_include = _remove_brands(state.brand_include, state.brand_exclude or [])
    if explicit.get("price_min") is not None:
        state.price_min_cny = float(explicit["price_min"])
    if explicit.get("price_max") is not None:
        state.price_max_cny = float(explicit["price_max"])


def _ordered_union(existing: list[str] | None, incoming: list[str]) -> list[str]:
    return list(dict.fromkeys([*(existing or []), *incoming]))


def _remove_brands(existing: list[str] | None, removed: list[str]) -> list[str] | None:
    removed_set = {brand.casefold() for brand in removed}
    kept = [brand for brand in (existing or []) if brand.casefold() not in removed_set]
    return kept or None
