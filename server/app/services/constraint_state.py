"""在多轮导购对话中解析检索过滤条件。

查询改写器(query rewriter)负责提供语义上下文;本模块负责管理结构化约束——
这些约束要么需要跨轮次持续生效,要么只能由用户显式改变。
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
_CLEAR_KEYWORDS_RE = re.compile(r"(?:国别不限|不限国别|取消国别|不限地区)")
_ALLOW_BRAND_RE = re.compile(r"(?:也可以|也行|可以接受|不排除|可以考虑)")
# R13.fix — 处理"来个更便宜的 / 换一个别的"这类追问(便宜很多 / 换个别的
# / 还有其他的)。当这种轮次自身没有提到任何品牌时,必须丢弃从前面轮次继承
# 来的品牌:用户想要的是**替代品**,而不是同一个(往往很贵的)品牌。
_RELAX_ALT_RE = re.compile(r"便宜很多|再便宜|换个?别的|换一个|其他的?|别的|另外")


def build_conversation_filter(
    messages: Iterable[ChatMessage],
    explicit: Mapping[str, Any] | None = None,
) -> Filter:
    """返回当前轮次的权威检索过滤条件。

    空的 ``Filter`` 是有意义的:它表示之前的约束已被取消,因此调用方不得再从
    改写后的查询(其中可能仍残留原始锚定文本)重新推断出已过期的约束。
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
    clear_keywords = bool(_CLEAR_KEYWORDS_RE.search(text))
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
    if clear_keywords:
        state.exclude_keywords = None

    turn = build_retrieval_filter(text)
    # R13.fix — 不带品牌的"便宜很多 / 换个别的 / 还有其他的"追问,要的是所指
    # 商品的**替代品**,所以从前面轮次继承的品牌不能继续生效(否则
    # "SK-II 神仙水… → 便宜很多的精华"会一直钉死在 SK-II 上,
    # 只返回那件贵的商品,找不到更便宜的)。
    if (not clear_brand and state.brand_include and _RELAX_ALT_RE.search(text)
            and not (turn and turn.brand_include)):
        state.brand_include = None

    if turn is None:
        return

    if not clear_category and turn.category:
        prev_category = state.category
        prev_sub_categories = state.sub_categories
        category_changed = bool(prev_category and prev_category != turn.category)
        # 判定真正的商品「领域」切换:要么 category 直接变了;要么之前的轮次只钉了
        # 子品类而没有 category(即泛指"鞋子"的规则),且这些继承下来的子品类在
        # 商品目录中都不属于新 category。靠目录中的 (category, sub_category)
        # 对照表,可以把 鞋子→化妆品(真切换)和 跑鞋→篮球鞋(同属 服饰运动,
        # 属于细化、必须保留预算)区分开。
        if category_changed:
            domain_switch = True
        elif not prev_category and prev_sub_categories:
            try:
                from rag.retrieve.constraints import _catalog_cat_subcats

                pairs = _catalog_cat_subcats()
                domain_switch = not any(
                    (turn.category, s) in pairs for s in prev_sub_categories
                )
            except Exception:
                domain_switch = False
        else:
            domain_switch = False

        state.category = turn.category
        # 发生真正的领域切换时,要丢弃子品类,同时也要丢弃切换轮次自己没有
        # 重新声明的继承预算——逛鞋子时设的"1000元以上"下限,不能悄悄过滤
        # 后面的"推荐化妆品"(否则 化妆品 会错误地只展示 ¥1000 以上的商品)。
        # 切换轮次自身若带了预算,会由下方的预算处理块重新生效。
        if domain_switch:
            if not turn.sub_category and not turn.sub_categories:
                state.sub_category = None
                state.sub_categories = None
            if not clear_budget:
                state.price_min_cny = None
                state.price_max_cny = None
        # R13 — 品类切换时还要丢弃在新品类下没有商品的继承品牌:iPad 轮次
        # 钉住了 brand=Apple,后面的"护肤品"轮次若不处理,就会把 brand=Apple
        # 继续 AND 到 美妆护肤 上 → 0 结果。
        if category_changed and state.brand_include and not turn.brand_include:
            try:
                from rag.retrieve.constraints import _catalog_brand_cats

                bcats = _catalog_brand_cats()
                kept = [
                    b for b in state.brand_include
                    if turn.category in bcats.get(b.casefold(), frozenset())
                ]
            except Exception:
                kept = []
            state.brand_include = kept or None
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

    # R8:国别关键词排除项与 brand_exclude 一样跨轮次持续生效。第 1 轮说的
    # "不要日系"在第 2 轮继续起作用,除非用户用"国别不限"显式取消。
    if not clear_keywords and turn.exclude_keywords:
        state.exclude_keywords = _ordered_union(state.exclude_keywords, list(turn.exclude_keywords))

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
