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

import hashlib
import os
import re
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Retrieval-result cache (R10 — "Option A").
#
# Problem it solves: the response cache in chat.py only short-circuits the
# LLM generation, AND it runs *after* retrieval. So even a repeat query
# pays the full hybrid + cross-encoder rerank cost every time — and the
# English path's v2-m3 reranker (568M params on a CPU VM) is the dominant
# latency. The chat response cache also keys on the whole conversation
# (messages_json), so it basically never hits in real multi-turn use.
#
# This cache memoizes the EXPENSIVE, preference-INDEPENDENT part of top_k
# (query expansion → hybrid → negation → rerank → anchor filter → hard
# constraints → price intent). The cheap, user-specific preference reorder
# (step 7) stays OUTSIDE the cache so 👍/👎 still re-orders live.
#
# Key = (resolved retrieval text, k, retrieval_filter repr, preference text).
# Deliberately NO user_id — preference is applied after the cache.
# TTL is short (default 300s) so FX-normalized prices in price-filtered
# results can't go stale beyond the FX layer's own 1h cache.
# ---------------------------------------------------------------------------

_RETRIEVAL_CACHE_TTL = float(os.getenv("RAG_RETRIEVAL_CACHE_TTL", "300"))
_RETRIEVAL_CACHE_MAX = int(os.getenv("RAG_RETRIEVAL_CACHE_MAX", "256"))
_RETRIEVAL_CACHE_ON = os.getenv("RAG_RETRIEVAL_CACHE", "1") == "1"
# product_id-keyed value list is cheap to copy; we store the raw dicts and
# hand back shallow copies so downstream preference reorder / truncation
# never mutates the cached entry.
_retrieval_cache: "dict[str, tuple[float, list[dict]]]" = {}
_retrieval_cache_lock = threading.Lock()
_retrieval_cache_stats = {"hits": 0, "misses": 0}


def _retrieval_cache_key(text: str, k: int, retrieval_filter, preference_text: str) -> str:
    # repr(Filter) is stable for a frozen-ish dataclass; None → "None".
    payload = f"{text}{k}{retrieval_filter!r}{preference_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _retrieval_cache_get(key: str) -> list[dict] | None:
    if not _RETRIEVAL_CACHE_ON:
        return None
    now = time.time()
    with _retrieval_cache_lock:
        entry = _retrieval_cache.get(key)
        if entry is None:
            _retrieval_cache_stats["misses"] += 1
            return None
        ts, value = entry
        if now - ts > _RETRIEVAL_CACHE_TTL:
            _retrieval_cache.pop(key, None)
            _retrieval_cache_stats["misses"] += 1
            return None
        _retrieval_cache_stats["hits"] += 1
        # Hand back shallow copies of each product dict so downstream
        # mutation (preference reorder truncates; nobody should mutate the
        # dicts, but be defensive) can't corrupt the cached list.
        return [dict(p) for p in value]


def _retrieval_cache_put(key: str, value: list[dict]) -> None:
    if not _RETRIEVAL_CACHE_ON:
        return
    with _retrieval_cache_lock:
        # Simple bound: clear oldest-ish by dropping arbitrary entries when full.
        if len(_retrieval_cache) >= _RETRIEVAL_CACHE_MAX:
            # Drop the chronologically oldest entry.
            oldest = min(_retrieval_cache.items(), key=lambda kv: kv[1][0], default=None)
            if oldest is not None:
                _retrieval_cache.pop(oldest[0], None)
        _retrieval_cache[key] = (time.time(), [dict(p) for p in value])


def retrieval_cache_stats() -> dict:
    with _retrieval_cache_lock:
        h = _retrieval_cache_stats["hits"]
        m = _retrieval_cache_stats["misses"]
        return {
            "retrieval_cache_size": len(_retrieval_cache),
            "retrieval_cache_max": _RETRIEVAL_CACHE_MAX,
            "retrieval_cache_ttl_sec": _RETRIEVAL_CACHE_TTL,
            "retrieval_cache_hits": h,
            "retrieval_cache_misses": m,
            "retrieval_cache_hit_rate": (h / (h + m)) if (h + m) else 0.0,
        }


def _negation_signals(text: str) -> bool:
    return any(s in text for s in (
        "不要", "别要", "别给我", "不想要", "不需要", "不考虑", "不买", "不选",
        "不含", "不带", "除了", "排除", "就算了", "就不看", "不用了", "no ", "without",
    ))


def _brand_match_terms(brand) -> set[str]:
    """Casefolded brand + alias terms, used to compare include vs exclude.

    Also expands the brand's individual whitespace/paren-split tokens: the
    catalog stores some brands as combined strings ("Apple 苹果", "华为（HUAWEI）")
    that have no alias cluster of their own, so without splitting they wouldn't
    merge with their own aliases ("Apple"/"苹果") and a single brand would look
    like several."""
    terms = {str(brand).casefold()}
    try:
        from rag.retrieve.brand_origin import expand_brand_aliases
        tokens = [brand] + str(brand).replace("（", " ").replace("）", " ").split()
        for tok in tokens:
            terms |= {str(t).casefold() for t in expand_brand_aliases(tok)}
    except Exception:
        pass
    return terms


def _reconcile_negation_with_includes(neg: dict, retrieval_filter) -> dict:
    """Drop from the negation set anything the user POSITIVELY asked for.

    "有没有华为手机,不要太贵的" puts 华为 in BOTH brand_include (the WHERE keeps
    only 华为) and — if the extractor over-reaches — exclude_brands (apply_negation
    then drops every 华为) → 0 results. A brand the user named, or the category
    they're shopping in, must never be excluded. Mutates and returns `neg`.
    """
    inc = getattr(retrieval_filter, "brand_include", None) or []
    if inc and neg.get("exclude_brands"):
        protected: set[str] = set()
        for b in inc:
            protected |= _brand_match_terms(b)
        neg["exclude_brands"] = [
            xb for xb in neg["exclude_brands"] if not (_brand_match_terms(xb) & protected)
        ]
    cat = getattr(retrieval_filter, "category", None)
    if cat and neg.get("exclude_categories"):
        neg["exclude_categories"] = [c for c in neg["exclude_categories"] if c != cat]
    return neg


def _distinct_brand_count(brand_include) -> int:
    """Count DISTINCT real brands, grouping aliases. The catalog stores some
    brands under several strings ("Apple 苹果" / "Apple" / "苹果"), so a plain
    len() over-counts and mis-classifies a single-brand query ("推荐iphone") as
    multi-brand — which then wrongly skips the product-line anchor filter and
    returns the whole Apple line instead of just iPhone."""
    groups: list[set[str]] = []
    for b in (brand_include or []):
        terms = _brand_match_terms(b)
        for g in groups:
            if g & terms:
                g |= terms
                break
        else:
            groups.append(set(terms))
    return len(groups)


def _ensure_brand_coverage(candidates: list[dict], named_brands: list[str],
                           anchors: tuple[str, ...] = (), top: int = 5) -> list[dict]:
    """Comparison coverage: keep each NAMED entity in the top `top`, so a reranker
    that doubles up on one doesn't drop another (a 4-brand compare losing 华为).
    Product-line-aware: when a line anchor is named ("iphone"), that entity is
    matched by TITLE token, not by brand — so "iphone华为小米" fills the Apple slot
    with an iPhone, not an iPad. Promotes a missing entity's best candidate into
    the top window, demoting the lowest slot of an over-represented brand."""
    anchor_set = {a.casefold() for a in anchors}
    # Build coverage entities: (kind, matcher). Line anchors match by title; a
    # brand that OWNS a present anchor (Apple owns iphone) is skipped — the
    # precise anchor entity covers it.
    entities: list[tuple[str, set[str]]] = [("title", {a}) for a in anchor_set]
    for nb in named_brands or []:
        terms = _brand_match_terms(nb)
        if anchor_set and (anchor_set & terms):
            continue
        entities.append(("brand", terms))
    # dedupe entities
    seen: list[set[str]] = []
    uniq = []
    for kind, m in entities:
        if any(m == s for s in seen):
            continue
        seen.append(m); uniq.append((kind, m))
    entities = uniq
    if len(candidates) <= top or len(entities) < 2:
        return candidates

    def _match(prod, kind, m):
        if kind == "title":
            t = (prod.get("title") or "").lower()
            return any(tok in t for tok in m)
        b = (prod.get("brand") or "").casefold()
        return any(tok and (tok in b or b in tok) for tok in m)

    head, tail = candidates[:top], candidates[top:]
    for kind, m in entities:
        if any(_match(p, kind, m) for p in head):
            continue
        idx = next((i for i, p in enumerate(tail) if _match(p, kind, m)), None)
        if idx is None:
            continue  # this entity has no product anywhere in the result set
        promote = tail.pop(idx)
        from collections import Counter
        hbrands = [(p.get("brand") or "").casefold() for p in head]
        cnt = Counter(hbrands)
        drop_i = next((i for i in range(len(head) - 1, -1, -1) if cnt[hbrands[i]] > 1), len(head) - 1)
        tail.insert(0, head.pop(drop_i))
        head.append(promote)
    return head + tail


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

# Comparison-intent markers. In a comparison the user wants to see ACROSS
# product lines / brands, so the single-line anchor filter must not narrow to
# one. Catches "对比X和Y", "X和Y哪个好", "X vs Y", "和华为比呢".
_COMPARISON_RE = re.compile(
    r"对比|对照|哪个|哪款|哪几款|vs\.?|相比|比一比|比较|[和跟与][^，。,；;]{1,16}比"
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


def _heavy_retrieve(
    text: str,
    retrieval_filter,
    preference_text: str,
    k: int,
    *,
    synonyms_on: bool,
    rewrite_on: bool,
    rerank_on: bool,
    negation_on: bool,
    price_on: bool,
) -> list[dict]:
    """The expensive, preference-INDEPENDENT retrieval pipeline (steps 1-6).

    Extracted from top_k (R10 Option A) so the result can be memoized in
    the retrieval cache. Inputs fully determine the output, EXCEPT the
    per-user 👍/👎 preference reorder which top_k applies afterward.
    Hybrid retrieve + the cross-encoder rerank live here — they're what
    the cache is built to skip on repeat queries.
    """
    from rag.retrieve.query import Filter

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
                    # R9.A.2 — attach retrieval signals (rrf_score,
                    # dense_rank, bm25_rank) onto the product dict so
                    # chat.py can surface them in the "why this is
                    # recommended" debug card. Stored in a private
                    # "_retrieval" key that chat.py strips before the
                    # client payload (only the cleaned subset goes to
                    # iOS). Copy the dict to avoid polluting the shared
                    # catalog stored in Chroma's row cache.
                    p = dict(h.product)
                    sig = p.setdefault("_retrieval", {})
                    sig["rrf_score"] = round(float(h.rrf_score), 4) if h.rrf_score else None
                    sig["dense_rank"] = h.dense_rank
                    sig["bm25_rank"] = h.bm25_rank
                    sig["query"] = q
                    seen[h.product_id] = p
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
            # Conflict guard: never exclude something the user EXPLICITLY asked
            # for (positive intent wins). See _reconcile_negation_with_includes.
            if isinstance(retrieval_filter, Filter):
                neg = _reconcile_negation_with_includes(neg, retrieval_filter)
            candidates = apply_negation(candidates, neg)
        except Exception:
            pass

    # 3.5) R11.fix — POSITIVE origin constraint ("要国产 / 国货"). The negation
    # extractor only handles 不要X / 除了X, so an implicit "国产" requirement
    # never dropped foreign brands (golden case 84 leaked HOKA/adidas/迪卡侬).
    # Standalone filter, applied before rerank so the CN-only set is reranked.
    try:
        from rag.retrieve.negation import requires_domestic, apply_domestic_filter
        if requires_domestic(text):
            candidates = apply_domestic_filter(candidates)
    except Exception:
        pass

    # 3.6) R11.fix — "X以外 / X之外" excludes brand X (e.g. the multi-turn
    # follow-up "华为以外还有吗"). Scan the RAW current-turn message
    # (preference_text) AND the retrieval text, since the contextual rewrite
    # may drop the 以外 clause. Fail-soft (never strand the user).
    try:
        from rag.retrieve.negation import except_brands
        from rag.retrieve.brand_origin import expand_brand_aliases
        _exc = except_brands(preference_text) or except_brands(text)
        if _exc:
            _ex_set: set[str] = set()
            for b in _exc:
                _ex_set |= expand_brand_aliases(b)
            _filtered = [c for c in candidates
                         if not any(x and x in (c.get("brand") or "").lower() for x in _ex_set)]
            candidates = _filtered or candidates
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
    # R10.perf — cap how many candidates the cross-encoder scores. The
    # cross-encoder cost is ~linear in candidate count, and the hybrid
    # pool is already RRF-ordered, so the relevant items sit near the top.
    # Capping the rerank INPUT (not the output) trades a little tail recall
    # for a big CPU-latency cut on the VM. Env-tunable; 0 = no cap = the
    # original "rerank all ~20" behaviour.
    rerank_input_cap = int(os.getenv("RERANK_INPUT_CAP", "0"))
    if rerank_input_cap > 0 and len(candidates) > rerank_input_cap:
        candidates = candidates[:rerank_input_cap]
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
    # The product-line anchor filter is for SINGLE-line lookups ("iPhone13" →
    # not iPad). In a comparison ("iPhone和小米哪个好") or any query naming ≥2
    # brands it wrongly strips the OTHER brand → "目录里没有小米". Skip it there;
    # the reranker already surfaces both (verified live on the 145-item index).
    _is_comparison = bool(_COMPARISON_RE.search(text))
    _multi_brand = bool(retrieval_filter) and _distinct_brand_count(getattr(retrieval_filter, "brand_include", None)) >= 2
    if not (_is_comparison or _multi_brand):
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

    # Per-entity coverage: a comparison OR a bare multi-brand list ("iphone华为
    # 小米呢") should keep EACH named brand in the top-5, so the reranker doubling
    # up on one doesn't drop another (was losing iPhone here).
    if (_is_comparison or _multi_brand) and retrieval_filter and (getattr(retrieval_filter, "brand_include", None) or []):
        _anchors = tuple(a for a in _PRODUCT_LINE_ANCHORS if a in text.lower())
        candidates = _ensure_brand_coverage(candidates, retrieval_filter.brand_include, anchors=_anchors, top=5)

    return candidates


def top_k(
    text: str,
    k: int = 5,
    filters: dict | None = None,
    *,
    conversation_filter=None,
    intent_text: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    """Hybrid-retrieve + (optional) rewrite + negation-filter + rerank → top-k products.

    R9.B: when `user_id` is given, a gentle preference prior (from the
    user's 👍/👎 history) re-orders the final list before truncation.
    """
    synonyms_on = os.getenv("RAG_SYNONYMS", "1") == "1"
    rewrite_on = os.getenv("RAG_REWRITE", "0") == "1"
    rerank_on = os.getenv("RAG_RERANK", "1") == "1"
    negation_on = (os.getenv("RAG_NEGATION", "1") == "1") and _negation_signals(text)
    price_on = os.getenv("RAG_PRICE_INTENT", "1") == "1"
    hard_filters_on = os.getenv("RAG_HARD_FILTERS", "1") == "1"

    from rag.retrieve.constraints import build_retrieval_filter
    from rag.retrieve.query import Filter

    # R8.F.7 — Topic-switch detection (generalized in R8.F.8).
    #
    # Original narrow version only caught Apple product-line anchors
    # (iPhone / iPad / MacBook / AirPods / ...). User feedback (and the
    # 'snacks after skincare' regression) showed that's whack-a-mole:
    # switching to "我想买点零食" or "Nike 跑鞋" still let the inherited
    # 美妆护肤 filter strand retrieval.
    #
    # Generalized to two complementary signals — either flips the switch:
    #
    #   Path A  Hard-coded product-LINE anchors (iPhone / iPad / etc.).
    #           These tokens are SKU-line names that build_retrieval_filter
    #           doesn't know how to map to a category. Keep the explicit
    #           list as a safety net.
    #
    #   Path B  Re-extract a Filter from the RAW current user message
    #           (intent_text, NOT the contextual-rewrite'd text). If it
    #           carries a category / sub_category / brand_include signal
    #           that differs from the inherited conversation_filter, the
    #           user has clearly named a new topic — reset.
    #
    # Either path firing drops conversation_filter AND substitutes the
    # raw message for the rewritten text. "再便宜点的"-style follow-ups
    # (no category/brand signal of their own) still inherit normally.
    raw_message_for_anchor = intent_text or text or ""
    topic_switch = False

    # Path A: explicit product-line anchor.
    if raw_message_for_anchor and any(
        a in raw_message_for_anchor.lower() for a in _PRODUCT_LINE_ANCHORS
    ):
        topic_switch = True

    # Path B (R8.F.8.1, expanded): conflict-check against inherited filter
    # on EITHER category OR brand. Earlier version only checked category,
    # which let inherited brand_include = ["Apple"] (from a prior iPad
    # turn) keep filtering retrieval even when the new query carried a
    # clear category signal — that was the "护肤品 / 鞋子 / 纸尿片 returns
    # 0 results after iPad turns" failure.
    if not topic_switch and isinstance(conversation_filter, Filter) and raw_message_for_anchor:
        try:
            raw_filter = build_retrieval_filter(raw_message_for_anchor, None)
        except Exception:
            raw_filter = None
        raw_cat = raw_filter.category if raw_filter else None
        if not raw_cat:
            try:
                from rag.retrieve.constraints import detect_topic_switch_category
                raw_cat = detect_topic_switch_category(raw_message_for_anchor)
            except Exception:
                raw_cat = None
        raw_brands = set((raw_filter.brand_include or [])) if raw_filter else set()

        inh_cat = conversation_filter.category
        inh_brands = set((conversation_filter.brand_include or []))

        # Category conflict: raw has a new category signal that doesn't
        # match inherited (including "inherited had no category but
        # something else like brand was sticky").
        cat_conflict = bool(raw_cat and raw_cat != inh_cat)

        # Brand conflict: raw has brand_include and it doesn't intersect
        # inherited's. Triggers e.g. "推荐 OPPO 手机" after iPad turns
        # where conversation_filter inherited brand_include = ["Apple"].
        brand_conflict = bool(raw_brands and inh_brands and not (raw_brands & inh_brands))

        # Also: raw has a category but inherited has a brand_include of
        # a different ecosystem (typical: iPad turns leave brand=Apple,
        # then user says "护肤品" — different category, different brand).
        category_vs_brand_conflict = bool(
            raw_cat and inh_brands and not raw_brands and raw_cat != inh_cat
        )

        # R9.A.1 — Path C: sub_categories conflict.
        # The leakiest dimension per Sam's CONTEXT_CONTAMINATION_DIAGNOSIS.md.
        # Inherited sub_categories from an earlier turn (e.g. ['洁面']
        # from "推荐适合敏感肌的洁面") survive through unrelated turns
        # (iPad / 鞋子 / 纸尿片) because none of them produce a category
        # signal AND none mention 洁面. By turn 5, the final query
        # "护肤品" matches the inherited category (美妆护肤) so
        # cat_conflict above doesn't fire — but sub_categories=['洁面']
        # is still there, narrowing retrieval to a single product class.
        #
        # Detection: inherited has sub_categories AND
        #   (a) current raw turn extracts its OWN sub_categories that
        #       don't intersect the inherited ones, OR
        #   (b) current turn produced no sub_categories AND its text
        #       doesn't mention any inherited sub_category token AND
        #       it carries a fresh topical signal (category or brand).
        # Case (b) treats "iPad" / "护肤品" / etc. as topic switches
        # without false-positive-ing on "再便宜点的" follow-ups (which
        # have no category/brand signal of their own).
        inh_sub_cats = list(conversation_filter.sub_categories or [])
        if conversation_filter.sub_category and conversation_filter.sub_category not in inh_sub_cats:
            inh_sub_cats.append(conversation_filter.sub_category)
        raw_sub_cats: list[str] = []
        if raw_filter is not None:
            raw_sub_cats = list(raw_filter.sub_categories or [])
            if raw_filter.sub_category and raw_filter.sub_category not in raw_sub_cats:
                raw_sub_cats.append(raw_filter.sub_category)

        sub_conflict = False
        if inh_sub_cats:
            text_mentions_inherited = any(
                tok and tok in raw_message_for_anchor for tok in inh_sub_cats
            )
            if raw_sub_cats:
                # Case (a): both have sub_cats — conflict if no overlap.
                sub_conflict = not (set(raw_sub_cats) & set(inh_sub_cats))
            elif not text_mentions_inherited and (raw_cat or raw_brands):
                # Case (b): inherited has stale sub_cats, current turn
                # has a fresh topical signal but doesn't reference any
                # inherited sub_cat → topic switch.
                sub_conflict = True

        if cat_conflict or brand_conflict or category_vs_brand_conflict or sub_conflict:
            topic_switch = True

    if topic_switch:
        conversation_filter = None
        text = raw_message_for_anchor  # bypass the contextual-query rewriter

    if hard_filters_on and isinstance(conversation_filter, Filter):
        # Conversation state is authoritative, including an empty Filter after
        # the user explicitly cancels conditions inherited from earlier turns.
        retrieval_filter = conversation_filter
    else:
        retrieval_filter = build_retrieval_filter(text if hard_filters_on else "", filters)
    preference_text = intent_text if intent_text is not None else text

    # R10 Option A — retrieval-result cache. Steps 1-6 (query expansion →
    # hybrid → negation → rerank → anchor filter → hard constraints → price
    # intent) are expensive and preference-INDEPENDENT, so memoize them.
    # On a hit we skip hybrid + the v2-m3 cross-encoder entirely — that's
    # the dominant latency, especially on the English path. The cheap,
    # user-specific preference reorder (step 7) stays below, OUTSIDE the
    # cache, so 👍/👎 still re-orders live and proposal #12 is preserved.
    _rc_key = _retrieval_cache_key(text, k, retrieval_filter, preference_text)
    candidates = _retrieval_cache_get(_rc_key)
    if candidates is None:
        candidates = _heavy_retrieve(
            text,
            retrieval_filter,
            preference_text,
            k,
            synonyms_on=synonyms_on,
            rewrite_on=rewrite_on,
            rerank_on=rerank_on,
            negation_on=negation_on,
            price_on=price_on,
        )
        _retrieval_cache_put(_rc_key, candidates)

    # 7) R9.B — closed-loop preference prior (proposal #12). Gentle,
    # bounded re-order by the user's 👍/👎 history. No-ops when the user
    # has no recorded preferences. Applied LAST so relevance + hard
    # constraints are already settled; preference only nudges near-ties.
    pref_on = os.getenv("RAG_PREFERENCES", "1") == "1"
    if pref_on and user_id:
        try:
            from app.services.preferences_db import get_weights
            from rag.retrieve.preferences import apply_preference_prior

            weights = get_weights(user_id)
            candidates = apply_preference_prior(candidates, weights)
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
