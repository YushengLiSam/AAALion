"""Backend's view of the RAG layer. Combines hybrid retrieval (dense + BM25),
optional query rewriting, negation filtering, and cross-encoder reranking.

The default path is:
  user_text → parse hard constraints → curated synonyms → (optional) rewrite
            → filtered hybrid retrieve top-20 → apply negation → rerank
            → enforce converted-CNY budget / price preference → top-k products.

Toggle via env vars:
  RAG_SYNONYMS=1 enable curated local query expansion (default on)
  RAG_REWRITE=1   enable LLM query expansion (default off — costs API calls)
  RAG_NEGATION=1  enable LLM negation extraction (auto-on when 不要/除了/不含 in query)
  RAG_RERANK=1    enable cross-encoder rerank (default on)
  RAG_HARD_FILTERS=1 enable inferred category/brand/CNY-budget retrieval filters (default on)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _negation_signals(text: str) -> bool:
    return any(s in text for s in ("不要", "不含", "不带", "除了", "排除", "no ", "without"))


# R8.F.6: well-known Apple product-line tokens the user might type. When
# any of these shows up in the query, the result list is filtered to only
# products whose title contains the same token (case-insensitive). This
# fixes the "iPhone13" → iPad Pro 13英寸 cross-confusion where the digit
# "13" matched screen size everywhere.
#
# Conservative list — only product LINES with one-token names that
# unambiguously identify the line. "Apple" / "苹果" alone are too broad
# (the user could mean any Apple product). Galaxy / Pixel etc. can be
# added when the catalog grows to include them.
_PRODUCT_LINE_ANCHORS: tuple[str, ...] = (
    "iphone", "ipad", "macbook", "airpods", "homepod",
    "imac", "mac mini", "mac studio", "mac pro", "vision pro",
    "apple watch",
)


def _filter_by_product_line(text: str, candidates: list[dict]) -> list[dict]:
    """If the user typed a known product-line anchor (e.g. "iPhone"),
    drop candidates whose title doesn't contain that token. Fail-soft:
    if filtering leaves nothing, return the original list unchanged so
    the user always gets something on screen.
    """
    if not text or not candidates:
        return candidates
    text_lower = text.lower()
    matched = [a for a in _PRODUCT_LINE_ANCHORS if a in text_lower]
    if not matched:
        return candidates
    filtered: list[dict] = []
    for p in candidates:
        title = (p.get("title") or "").lower()
        if any(a in title for a in matched):
            filtered.append(p)
    return filtered if filtered else candidates


def _is_specific_query(text: str) -> bool:
    """Fast-path detector: when the query mentions a known catalog brand,
    dense + BM25 hybrid already converges strongly enough that the
    cross-encoder rerank rarely changes the top-k. Skipping rerank for
    these queries cuts median latency from ~2s to ~300ms with no measurable
    recall regression on Sam's 56-case eval.

    Falls back to "not specific" on any error so the rerank still runs.
    """
    if not text:
        return False
    try:
        from rag.retrieve.brand_origin import BRAND_ORIGIN
        text_lower = text.lower()
        # Direct brand mentions — strong signal.
        for brand in BRAND_ORIGIN:
            if len(brand) >= 2 and brand.lower() in text_lower:
                return True
    except Exception:
        return False
    return False


def top_k(
    text: str,
    k: int = 5,
    filters: dict | None = None,
    *,
    conversation_filter=None,
    intent_text: str | None = None,
) -> list[dict]:
    """Hybrid-retrieve + (optional) rewrite + negation-filter + rerank → top-k products."""
    synonyms_on = os.getenv("RAG_SYNONYMS", "1") == "1"
    rewrite_on = os.getenv("RAG_REWRITE", "0") == "1"
    rerank_on = os.getenv("RAG_RERANK", "1") == "1"
    negation_on = (os.getenv("RAG_NEGATION", "1") == "1") and _negation_signals(text)
    price_on = os.getenv("RAG_PRICE_INTENT", "1") == "1"
    hard_filters_on = os.getenv("RAG_HARD_FILTERS", "1") == "1"

    from rag.retrieve.constraints import build_retrieval_filter
    from rag.retrieve.query import Filter

    if hard_filters_on and isinstance(conversation_filter, Filter):
        # Conversation state is authoritative, including an empty Filter after
        # the user explicitly cancels conditions inherited from earlier turns.
        retrieval_filter = conversation_filter
    else:
        retrieval_filter = build_retrieval_filter(text if hard_filters_on else "", filters)
    preference_text = intent_text if intent_text is not None else text

    # 1) Curated local expansion, then optional LLM rewrite to multi-query.
    queries: list[str] = [text]
    if synonyms_on:
        try:
            from rag.retrieve.synonyms import expand_query

            queries = expand_query(text) or [text]
        except Exception:
            queries = [text]
    if rewrite_on:
        try:
            from rag.retrieve.rewrite import rewrite_query

            queries = _dedupe_queries(queries + (rewrite_query(text) or [])) or [text]
        except Exception:
            queries = _dedupe_queries(queries) or [text]

    # 2) Hybrid retrieve top-20 across all queries, dedupe by product_id.
    try:
        from rag.retrieve.hybrid import hybrid_topk
        seen: dict[str, dict] = {}
        for q in queries:
            for h in hybrid_topk(q, k=20, f=retrieval_filter):
                if h.product_id not in seen:
                    seen[h.product_id] = h.product
        candidates = list(seen.values())
    except Exception:
        try:
            from rag.retrieve.query import query
            candidates = [h.product for h in query(text, k=20, f=retrieval_filter)]
        except Exception:
            from rag.retrieve.query import _keyword_fallback  # type: ignore

            candidates = [h.product for h in _keyword_fallback(text, k=20, f=retrieval_filter)]

    # 3) Negation filter (drops violating candidates).
    # R8: if the current turn has 不要, run the LLM-or-local negation extractor.
    # Otherwise, if conversation_filter carries `exclude_keywords` from prior
    # turns (e.g. "不要日系" said in turn 1, current turn is "再便宜点的呢"),
    # still apply those keyword exclusions so country bans persist.
    inherited_keywords: list[str] = []
    if isinstance(retrieval_filter, Filter) and retrieval_filter.exclude_keywords:
        inherited_keywords = list(retrieval_filter.exclude_keywords)
    if negation_on or inherited_keywords:
        try:
            from rag.retrieve.negation import apply_negation, extract_negation
            if negation_on:
                neg = extract_negation(text)
                # Union the inherited keywords so prior-turn negations still apply.
                if inherited_keywords:
                    existing = set(neg.get("exclude_keywords", []) or [])
                    for kw in inherited_keywords:
                        if kw not in existing:
                            neg.setdefault("exclude_keywords", []).append(kw)
            else:
                neg = {
                    "exclude_brands": [],
                    "exclude_categories": [],
                    "exclude_keywords": inherited_keywords,
                }
            candidates = apply_negation(candidates, neg)
        except Exception:
            pass

    # 4) Rerank with cross-encoder. Keep a slightly larger pool when price
    # intent may reorder candidates into the final top-k.
    # Fast-path (env-toggleable via RAG_FAST_PATH, default ON): skip rerank
    # for brand-specific queries — dense+BM25 already nails those.
    # IMPORTANT: never skip rerank when the query is a negation. Negation
    # cases mention brands to EXCLUDE, and rerank is what pushes the right
    # alternatives to the top (eval shows neg-acc 0.733 → 0.667 if we skip).
    has_price_filter = bool(retrieval_filter and retrieval_filter.has_price_constraint)
    rerank_limit = max(k, 20) if has_price_filter else (max(k, 10) if price_on else k)
    fast_path_on = os.getenv("RAG_FAST_PATH", "1") == "1"
    skip_rerank = fast_path_on and _is_specific_query(text) and not negation_on
    if rerank_on and len(candidates) > k and not skip_rerank:
        try:
            from rag.retrieve.rerank import rerank
            candidates = rerank(text, candidates, top_k=rerank_limit)
        except Exception:
            candidates = candidates[:rerank_limit]
    else:
        candidates = candidates[:rerank_limit]

    # 4.5) Product-line anchor filter (R8.F.6).
    #
    # User typed "iPhone13" / "iPhone 13" and got iPad Pro 13英寸. Root cause:
    # the digit "13" tokenizes the same as "13英寸" (screen size), so iPad
    # Pro / MacBook 13 inch products scored high on BM25 *and* the
    # cross-encoder couldn't separate "iPhone 13 model" from "iPad 13 inch"
    # semantically — both look like "Apple device, 13".
    #
    # Fix is product-line aware: if the user explicitly named a product line
    # (iPhone / iPad / MacBook / AirPods / Watch), require that token to
    # appear in the result title. Fail-soft: if the filter would empty the
    # list, keep the original rerank result (we never strand the user).
    candidates = _filter_by_product_line(text, candidates)

    # 5) Re-check hard constraints after retrieval. Foreign-source products
    # receive live CNY values only here, so RMB budgets become strict now.
    if retrieval_filter:
        from rag.retrieve.query import apply_product_filter
        if has_price_filter:
            from app.services.currency import normalize_product_prices

            candidates = normalize_product_prices(candidates)
        candidates = apply_product_filter(candidates, retrieval_filter, strict_cny_price=True)

    # 6) Price intent is a preference layer after hard constraints.
    if price_on:
        try:
            from app.services.price_intent import apply_price_intent, parse_price_intent
            if parse_price_intent(preference_text).active and not has_price_filter:
                from app.services.currency import normalize_product_prices

                candidates = normalize_product_prices(candidates)
            candidates = apply_price_intent(
                candidates,
                preference_text,
                enforce_ranges=not has_price_filter,
            )
        except Exception:
            pass

    return candidates[:k]


def top_k_image(image_bytes: bytes, k: int = 3) -> list[dict]:
    """Visually similar top-k via CLIP. Empty list if CLIP isn't available."""
    try:
        from rag.retrieve.query import query_image
        hits = query_image(image_bytes, k=k)
        return [h.product for h in hits]
    except Exception:
        return []


stub_top_k = top_k


def _dedupe_queries(queries: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = (query or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
