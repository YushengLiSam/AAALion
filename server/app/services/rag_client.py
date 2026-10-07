"""后端视角的 RAG 层。组合了混合检索(稠密向量 + BM25)、可选的查询改写、
否定过滤,以及交叉编码器(cross-encoder)重排序。

默认链路为:
  user_text → 解析硬约束 → 人工维护的同义词扩展 → (可选)改写
            → 带过滤的混合检索 top-20 → 应用否定过滤 → 重排序
            → 强制执行折算为人民币的预算/价格偏好 → top-k 商品。

通过环境变量开关:
  RAG_SYNONYMS=1 启用人工维护的本地查询扩展(默认开)
  RAG_REWRITE=1   启用 LLM 查询扩展(默认关——会消耗 API 调用)
  RAG_NEGATION=1  启用 LLM 否定提取(查询中含 不要/除了/不含 时自动开启)
  RAG_RERANK=1    启用交叉编码器重排序(默认开)
  RAG_HARD_FILTERS=1 启用推断出的类目/品牌/人民币预算检索过滤(默认开)
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
# 检索结果缓存(R10 ——「方案 A」)。
#
# 它解决的问题:chat.py 里的响应缓存只能短路 LLM 生成,而且是在检索
# *之后*才生效。因此即使是重复查询,每次也要付出完整的混合检索 +
# 交叉编码器重排序的成本——而英文链路用的 v2-m3 重排模型(568M 参数,
# 跑在 CPU VM 上)正是延迟的大头。聊天响应缓存还以整段对话
# (messages_json)作为键,在真实多轮使用中基本永远命中不了。
#
# 这个缓存把 top_k 中昂贵且与偏好无关的部分(查询扩展 → 混合检索 →
# 否定过滤 → 重排序 → 锚点过滤 → 硬约束 → 价格意图)做了 memoize。
# 廉价、用户相关的偏好重排(第 7 步)留在缓存之外,
# 这样 👍/👎 仍能实时调整顺序。
#
# 键 = (解析后的检索文本, k, retrieval_filter 的 repr, 偏好文本)。
# 有意不包含 user_id——偏好是在缓存之后才应用的。
# TTL 设得很短(默认 300s),保证价格过滤结果中按汇率折算的价格
# 不会比 FX 层自身的 1 小时缓存更陈旧。
# ---------------------------------------------------------------------------

_RETRIEVAL_CACHE_TTL = float(os.getenv("RAG_RETRIEVAL_CACHE_TTL", "300"))
_RETRIEVAL_CACHE_MAX = int(os.getenv("RAG_RETRIEVAL_CACHE_MAX", "256"))
_RETRIEVAL_CACHE_ON = os.getenv("RAG_RETRIEVAL_CACHE", "1") == "1"
# 以 product_id 为键的值列表拷贝成本很低;我们存的是原始 dict,
# 取出时交回浅拷贝,这样下游的偏好重排/截断
# 永远不会改动缓存中的条目。
_retrieval_cache: "dict[str, tuple[float, list[dict]]]" = {}
_retrieval_cache_lock = threading.Lock()
_retrieval_cache_stats = {"hits": 0, "misses": 0}


def _retrieval_cache_key(text: str, k: int, retrieval_filter, preference_text: str) -> str:
    # 对近似 frozen 的 dataclass 来说 repr(Filter) 是稳定的;None → "None"。
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
        # 交回每个商品 dict 的浅拷贝,这样下游的修改(偏好重排会截断;
        # 按理没人该改这些 dict,但出于防御性考虑)
        # 不会污染缓存中的列表。
        return [dict(p) for p in value]


def _retrieval_cache_put(key: str, value: list[dict]) -> None:
    if not _RETRIEVAL_CACHE_ON:
        return
    with _retrieval_cache_lock:
        # 简单的容量上限:满了就丢条目腾位。
        if len(_retrieval_cache) >= _RETRIEVAL_CACHE_MAX:
            # 丢弃时间上最老的那一条。
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


# 不能被算作「查询与该商品有词面重叠」的虚词。
_OVERLAP_EN_STOP = frozenset({
    "the", "for", "and", "with", "under", "below", "over", "than", "less",
    "more", "best", "cheap", "cheaper", "recommend", "want", "need", "buy",
    "get", "please", "ones", "one", "any", "good", "some", "show", "give",
})


def _lexical_overlap(query: str, candidates: list[dict], top_n: int = 5) -> bool:
    """当任一靠前候选的标题/品牌与查询共享实义 token(CJK 二元组,或长度
    ≥3 且非虚词的 ASCII 单词)时返回 True。用作「无匹配」判定的一票否决
    (VETO):只要有词面依据,就说明候选池不是垃圾,即便交叉编码器
    对简短措辞("要折叠屏")打分偏冷。
    查询完全没有实义 token 时返回 True(不否决)。"""
    q = query or ""
    grams: set[str] = set()
    for m in re.finditer(r"[一-鿿]{2,}", q):
        s = m.group(0)
        grams |= {s[i:i + 2] for i in range(len(s) - 1)}
    words = {
        w for w in re.findall(r"[a-z]{3,}", q.lower())
        if w not in _OVERLAP_EN_STOP
    }
    if not grams and not words:
        return True
    for p in candidates[:top_n]:
        doc = f"{p.get('title', '')} {p.get('brand', '')}".lower()
        if any(g in doc for g in grams) or any(w in doc for w in words):
            return True
    return False


def _negation_signals(text: str) -> bool:
    return any(s in text for s in (
        "不要", "别要", "别给我", "不想要", "不需要", "不考虑", "不买", "不选",
        "不含", "不带", "除了", "排除", "就算了", "就不看", "不用了", "no ", "without",
    ))


def _brand_match_terms(brand) -> set[str]:
    """casefold 后的品牌名 + 别名词集合,用于比较 include 与 exclude。

    同时会对品牌按空格/括号拆出的各个 token 做扩展:目录里有些品牌
    存成了组合字符串("Apple 苹果"、"华为（HUAWEI）"),本身没有对应的
    别名簇,不拆分的话它们就无法与自己的别名("Apple"/"苹果")归并,
    导致同一个品牌看起来像好几个。"""
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
    """把用户明确正向要求的东西从否定集合里剔除。

    "有没有华为手机,不要太贵的" 会让 华为 同时进入 brand_include(WHERE 只保留
    华为)和——如果提取器越界的话——exclude_brands(apply_negation 随后把所有
    华为 全部丢掉)→ 0 条结果。用户点名的品牌、或正在选购的类目,绝不能被
    排除。原地修改并返回 `neg`。
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
    """统计去重后的真实品牌数,把别名归为一组。目录里有些品牌以多个字符串
    形式存在("Apple 苹果" / "Apple" / "苹果"),直接 len() 会数多,把单品牌
    查询("推荐iphone")误判成多品牌——进而错误地跳过产品线锚点过滤,
    返回整个 Apple 产品线而不是只返回 iPhone。"""
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


_CATALOG_CACHE: "list[dict] | None" = None
_CATALOG_LOCK = threading.Lock()


def _catalog_index() -> list[dict]:
    """全部目录商品(完整 dict),只加载一次。用作兜底数据源,
    保证被点名却没出现在检索池中的品牌/产品线仍然能展示出来。"""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        with _CATALOG_LOCK:
            if _CATALOG_CACHE is None:
                import glob
                import json
                items: list[dict] = []
                root = Path(__file__).resolve().parents[3]
                for p in glob.glob(str(root / "data" / "seed" / "*" / "data" / "*.json")):
                    try:
                        d = json.load(open(p, encoding="utf-8"))
                    except Exception:
                        continue
                    if d.get("product_id"):
                        d.setdefault("_retrieval", {"source": "catalog"})
                        items.append(d)
                _CATALOG_CACHE = items
    return _CATALOG_CACHE


def _entity_match(prod: dict, kind: str, m: set[str]) -> bool:
    if kind == "title":
        t = (prod.get("title") or "").lower()
        return any(tok in t for tok in m)
    b = (prod.get("brand") or "").casefold()
    return any(tok and (tok in b or b in tok) for tok in m)


def _catalog_fallback(kind: str, m: set[str], exclude_ids: set, prefer_subcats: set) -> dict | None:
    """为没出现在结果池中的被点名实体挑选目录里最合适的商品。
    优先选 sub_category 与结果中主流子类一致的商品
    (这样手机列表里 Apple 的位置由 iPhone——而不是 MacBook——来补)。"""
    cands = [p for p in _catalog_index()
             if p.get("product_id") not in exclude_ids and _entity_match(p, kind, m)]
    if not cands:
        return None
    cands.sort(key=lambda p: 0 if (prefer_subcats and p.get("sub_category") in prefer_subcats) else 1)
    return dict(cands[0])


def _ensure_brand_coverage(candidates: list[dict], named_brands: list[str],
                           anchors: tuple[str, ...] = (), top: int = 5) -> list[dict]:
    """对比场景的覆盖保障:让每个被点名的实体都留在前 `top` 名里,避免重排器
    在某一个上重复下注而挤掉另一个(四品牌对比时丢掉 华为)。
    具备产品线感知:点名了产品线锚点("iphone")时,该实体按标题 token 匹配
    而不是按品牌——这样 "iphone华为小米" 里 Apple 的位置由 iPhone 补上,而不是
    iPad。把缺席实体的最佳候选提升进头部窗口,
    同时把占位过多品牌的最低名次降下去。"""
    anchor_set = {a.casefold() for a in anchors}
    # 构建覆盖实体:(kind, matcher)。产品线锚点按标题匹配;拥有在场锚点的
    # 品牌(Apple 拥有 iphone)会被跳过——
    # 更精确的锚点实体已经覆盖了它。
    entities: list[tuple[str, set[str]]] = [("title", {a}) for a in anchor_set]
    for nb in named_brands or []:
        terms = _brand_match_terms(nb)
        if anchor_set and (anchor_set & terms):
            continue
        entities.append(("brand", terms))
    # 实体去重
    seen: list[set[str]] = []
    uniq = []
    for kind, m in entities:
        if any(m == s for s in seen):
            continue
        seen.append(m); uniq.append((kind, m))
    entities = uniq
    if not candidates or len(entities) < 2:
        return candidates

    from collections import Counter
    head, tail = candidates[:top], candidates[top:]
    prefer_subcats = {s for s, _ in Counter(
        p.get("sub_category") for p in head if p.get("sub_category")).most_common(2)}
    for kind, m in entities:
        if any(_entity_match(p, kind, m) for p in head):
            continue
        idx = next((i for i, p in enumerate(tail) if _entity_match(p, kind, m)), None)
        if idx is not None:
            promote = tail.pop(idx)
        else:
            # (b) 目录兜底:被点名的实体在检索池里完全找不到——
            # 那就从全量目录里取它最合适的商品,保证点名的品牌/产品线
            # 永远不会被回答成"目录里没有X"。
            existing = {p.get("product_id") for p in head + tail}
            promote = _catalog_fallback(kind, m, existing, prefer_subcats)
            if promote is None:
                continue
        if len(head) >= top:
            hbrands = [(p.get("brand") or "").casefold() for p in head]
            cnt = Counter(hbrands)
            drop_i = next((i for i in range(len(head) - 1, -1, -1) if cnt[hbrands[i]] > 1), len(head) - 1)
            tail.insert(0, head.pop(drop_i))
        head.append(promote)
    return head + tail


# R8.F.6:用户可能输入的知名 Apple 产品线 token。只要其中任何一个出现在
# 查询里,结果列表就会被过滤为标题包含同一 token 的商品(不区分大小写)。
# 这修复了 "iPhone13" → iPad Pro 13英寸 的交叉混淆:
# 数字 "13" 在哪里都能匹配到屏幕尺寸。
#
# 这是一份保守的列表——只收录单个 token 就能无歧义识别的产品线(LINE)。
# 单独的 "Apple" / "苹果" 太宽泛(用户可能指任何 Apple 产品)。
# Galaxy / Pixel 等可以等目录扩充收录后再加进来。
_PRODUCT_LINE_ANCHORS: tuple[str, ...] = (
    "iphone", "ipad", "macbook", "airpods", "homepod",
    "imac", "mac mini", "mac studio", "mac pro", "vision pro",
    "apple watch",
)

# 对比意图标记。对比时用户想跨产品线/品牌看结果,
# 所以单产品线锚点过滤不能把范围收窄到一条线。
# 能捕获 "对比X和Y"、"X和Y哪个好"、"X vs Y"、"和华为比呢"。
_COMPARISON_RE = re.compile(
    r"对比|对照|哪个|哪款|哪几款|vs\.?|相比|比一比|比较|[和跟与][^，。,；;]{1,16}比"
)


def _filter_by_product_line(text: str, candidates: list[dict]) -> list[dict]:
    """如果用户输入了已知的产品线锚点(如 "iPhone"),
    就丢弃标题中不含该 token 的候选。软失败(fail-soft):
    若过滤后一个不剩,则原样返回原始列表,
    保证用户屏幕上总有东西可看。
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
    """快速路径检测器:当查询提到了目录中的已知品牌时,稠密向量 + BM25
    的混合检索已经收敛得足够好,交叉编码器重排序很少会改变 top-k。
    对这类查询跳过重排序,可把中位延迟从约 2s 降到约 300ms,
    且在 Sam 的 56 例评测集上没有可测出的召回回退。

    出现任何报错都回退为「非特定查询」,保证重排序照常运行。
    """
    if not text:
        return False
    try:
        from rag.retrieve.brand_origin import BRAND_ORIGIN
        text_lower = text.lower()
        # 直接提到品牌——强信号。ASCII 品牌名需要字母边界,
        # 防止短别名("mi"/"hp"/"nb")在普通英文单词内部误触发
        # ("programming" 并不是在提 小米)。
        for brand in BRAND_ORIGIN:
            b = brand.lower()
            if len(b) < 2:
                continue
            if b.isascii():
                if re.search(rf"(?<![a-z]){re.escape(b)}(?![a-z])", text_lower):
                    return True
            elif b in text_lower:
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
    relevance_gate: bool = True,
) -> list[dict]:
    """昂贵且与偏好无关的检索流水线(第 1-6 步)。

    从 top_k 中抽取出来(R10 方案 A),以便结果能被检索缓存 memoize。
    输出完全由输入决定,唯一例外是按用户 👍/👎 历史做的偏好重排——
    那一步由 top_k 在之后施加。
    混合检索 + 交叉编码器重排序都在这里——
    它们正是这个缓存在重复查询时要跳过的部分。
    """
    from rag.retrieve.query import Filter

    # 1) 人工维护的本地扩展,然后可选地用 LLM 改写成多查询(multi-query)。
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

    # 2) 对所有查询做混合检索 top-20,按 product_id 去重。
    try:
        from rag.retrieve.hybrid import hybrid_topk
        seen: dict[str, dict] = {}
        for q in queries:
            for h in hybrid_topk(q, k=20, f=retrieval_filter):
                if h.product_id not in seen:
                    # R9.A.2 —— 把检索信号(rrf_score、dense_rank、
                    # bm25_rank)挂到商品 dict 上,让 chat.py 能在
                    # 「为什么推荐这个」调试卡片里展示出来。存放在私有的
                    # "_retrieval" 键里,chat.py 会在组装客户端 payload 前
                    # 把它剥掉(只有清理后的子集会发到 iOS)。
                    # 复制 dict,避免污染 Chroma 行缓存中
                    # 共享的目录数据。
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

    # 3) 否定过滤(丢弃违反约束的候选)。
    # R8:当前轮里出现 不要 时,运行 LLM 或本地的否定提取器。
    # 否则,如果 conversation_filter 携带了之前轮次的 `exclude_keywords`
    # (例如第 1 轮说了"不要日系",当前轮是"再便宜点的呢"),
    # 仍要应用这些关键词排除,让产地禁令在多轮间持续生效。
    inherited_keywords: list[str] = []
    if isinstance(retrieval_filter, Filter) and retrieval_filter.exclude_keywords:
        inherited_keywords = list(retrieval_filter.exclude_keywords)
    if negation_on or inherited_keywords:
        try:
            from rag.retrieve.negation import apply_negation, extract_negation
            if negation_on:
                neg = extract_negation(text)
                # 把继承的关键词并进来,使之前轮次的否定仍然生效。
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
            # 冲突保护:绝不排除用户明确要求的东西
            # (正向意图优先)。见 _reconcile_negation_with_includes。
            if isinstance(retrieval_filter, Filter):
                neg = _reconcile_negation_with_includes(neg, retrieval_filter)
            candidates = apply_negation(candidates, neg)
        except Exception:
            pass

    # 3.5) R11.fix —— 正向产地约束("要国产 / 国货")。否定提取器只处理
    # 不要X / 除了X,所以隐式的"国产"要求从来没把外国品牌过滤掉
    # (golden 案例 84 泄漏了 HOKA/adidas/迪卡侬)。
    # 这是独立的过滤器,在重排序前应用,这样被重排的就是只含国产品牌的集合。
    try:
        from rag.retrieve.negation import requires_domestic, apply_domestic_filter
        if requires_domestic(text):
            candidates = apply_domestic_filter(candidates)
    except Exception:
        pass

    # 3.6) R11.fix —— "X以外 / X之外" 表示排除品牌 X(例如多轮追问
    # "华为以外还有吗")。要同时扫描当前轮的原始消息(preference_text)
    # 和检索文本,因为上下文改写可能会把 以外 从句丢掉。
    # 软失败(绝不让用户两手空空)。
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

    # 4) 用交叉编码器重排序。当价格意图可能把候选重新排进最终 top-k 时,
    # 保留一个略大的候选池。
    # 快速路径(由环境变量 RAG_FAST_PATH 开关,默认开):品牌特定的查询
    # 跳过重排序——dense+BM25 已经足以搞定它们。
    # 重要:查询带否定时绝不能跳过重排序。否定场景提到的品牌是要排除的,
    # 而正是重排序把正确的替代品推到前面
    # (评测显示一旦跳过,否定准确率从 0.733 掉到 0.667)。
    has_price_filter = bool(retrieval_filter and retrieval_filter.has_price_constraint)
    rerank_limit = max(k, 20) if has_price_filter else (max(k, 10) if price_on else k)
    fast_path_on = os.getenv("RAG_FAST_PATH", "1") == "1"
    skip_rerank = fast_path_on and _is_specific_query(text) and not negation_on
    # R10.perf —— 限制交叉编码器要打分的候选数量。交叉编码器的开销与候选数
    # 大致呈线性,而混合检索池已经按 RRF 排好序,相关条目就在前部。
    # 限制重排序的输入(而不是输出),用一点尾部召回换 VM 上一大截
    # CPU 延迟。可用环境变量调节;0 = 不限制 = 原本的
    # 「全部 ~20 条都重排」行为。
    rerank_input_cap = int(os.getenv("RERANK_INPUT_CAP", "0"))
    if rerank_input_cap > 0 and len(candidates) > rerank_input_cap:
        candidates = candidates[:rerank_input_cap]
    # R13 —— 原本的条件是 `len(candidates) > k`(「没东西可砍,省掉这笔开销」),
    # 但下面的相关性闸门即使候选池很小也需要交叉编码器的分数:硬过滤
    # 可能把池子收窄到 ≤k 个垃圾条目("家居香薰" → 5 个家居家具商品,
    # 没有一个是香薰),没过闸门的小池子曾被直接流式发给客户端。
    # 给 ≤k 个文本对打分只有微秒级开销。
    did_rerank = rerank_on and len(candidates) > 1 and not skip_rerank
    if did_rerank:
        try:
            from rag.retrieve.rerank import rerank
            candidates = rerank(text, candidates, top_k=rerank_limit)
        except Exception:
            candidates = candidates[:rerank_limit]
            did_rerank = False
    else:
        candidates = candidates[:rerank_limit]

    # 4.2) R13 C 类问题修复 —— 给重排后的候选池加相关性闸门。top_k 过去
    # 无论多不相关都返回 k 张卡片:"医用制氧机" 流出了 1 个真匹配外加
    # 4 张随机护肤卡;"电饭煲"(目录里根本没有)流出了 5 张面条/酱油卡,
    # 而 LLM 文本却说"没有"。
    #
    # 绝对分数在不同查询形态之间分不干净(golden 回归:"要折叠屏" 的相关项
    # top 是 0.04,电饭煲 的垃圾项 top 也是 0.04),所以闸门做了分层:
    #   * 无匹配下限(NO-MATCH floor)—— 返回 [] —— 仅当三个条件全部成立:
    #     (a) 没有具体的硬过滤信号塑造过候选池(来自查询或对话的子类/
    #         品牌/价格/排除条件,意味着像 "品牌不限,预算加到3500" 这种
    #         简短约束轮是被过滤器匹配上的,其文本-文档分数高低无所谓)。
    #         单独钉住一个大类目不算豁免:"家居香薰" 通过 家居 前缀钉住了
    #         家居家具,但这个货架上根本没有 香薰——这恰恰就是无匹配;
    #     (b) 没有词面重叠——查询的任何 CJK 二元组 / ASCII 单词都没出现在
    #         任何靠前候选的标题+品牌里("要折叠屏" 与 折叠屏手机 的标题
    #         有重叠,所以保住;电饭煲 与谁都不重叠);
    #     (c) 最高 sigmoid 分数低于按模型设定的垃圾下限(ZH base 模型:
    #         垃圾 ≤0.16;v2-m3 打分更冷,全垃圾时 ≤0.02)。
    #   * 尾部裁剪(TAIL trim)—— 仅当第一张卡本身已确信相关时才裁
    #     (top ≥ ceiling;否则整池都是冷分——否定措辞会让交叉编码器打分
    #     变冷,例如 "化妆水不要韩系" 最高分 0.08 而真答案只有 0.009——
    #     这时相对比值全是噪声)。top 分够热时,只有同时满足「远低于 top
    #     (score < top×ratio)」且「绝对值低到垃圾级(< ceiling)」才丢卡
    #     —— 制氧机 结果里搭车的护肤品(0.107 对 top 0.855)被裁掉,
    #     同货架的第二名得以幸存。
    # 重排序没运行时跳过(品牌快速路径——硬过滤本身已蕴含相关性);
    # 场景类查询也跳过(chat.py 传入 `relevance_gate=False`:
    # "三亚度假要准备什么" 合理地只拿 0.046 分,而且它就是想要
    # 跨类目的多样性)。
    if relevance_gate and did_rerank and candidates and os.getenv("RAG_RELEVANCE_GATE", "1") == "1":
        _sig0 = candidates[0].get("_retrieval") or {}
        _top_score = _sig0.get("rerank_score")
        if _top_score is not None:
            _multi = "v2-m3" in str(_sig0.get("rerank_model") or "")
            # ZH 下限取 0.10:目录里不存在的商品即便用偏热的 "推荐个X" 措辞,
            # 最高也只到 ≤0.08(香薰 0.060 / 电动牙刷 0.076 / 扫地机器人
            # 0.039);而正经的类目浏览既带过滤器(豁免)、分数又 ≥0.85。
            # 打分更冷的多语模型沿用 0.025。
            _floor = (float(os.getenv("RAG_NOMATCH_FLOOR_MULTI", "0.025")) if _multi
                      else float(os.getenv("RAG_NOMATCH_FLOOR_ZH", "0.10")))
            _filter_specific = bool(retrieval_filter is not None and (
                retrieval_filter.sub_categories or retrieval_filter.sub_category
                or retrieval_filter.brand_include or retrieval_filter.brand_exclude
                or retrieval_filter.exclude_keywords or retrieval_filter.has_price_constraint
            ))
            if (
                _top_score < _floor
                and not _filter_specific
                and not _lexical_overlap(f"{text} {preference_text}", candidates)
            ):
                return []
            _ceil = (float(os.getenv("RAG_TAIL_CEIL_MULTI", "0.03")) if _multi
                     else float(os.getenv("RAG_TAIL_CEIL_ZH", "0.12")))
            if _top_score >= _ceil:
                _cutoff = _top_score * float(os.getenv("RAG_CARD_TAIL_RATIO", "0.15"))
                candidates = [
                    c for c in candidates
                    if not (
                        ((c.get("_retrieval") or {}).get("rerank_score") or 0.0) < _cutoff
                        and ((c.get("_retrieval") or {}).get("rerank_score") or 0.0) < _ceil
                    )
                ]

    # 4.5) 产品线锚点过滤(R8.F.6)。
    #
    # 用户输入 "iPhone13" / "iPhone 13" 却拿到 iPad Pro 13英寸。根因:
    # 数字 "13" 的分词结果与 "13英寸"(屏幕尺寸)一样,于是 iPad Pro /
    # MacBook 13 英寸的商品在 BM25 上得分很高,*而且*交叉编码器在语义上
    # 也分不开 "iPhone 13 机型" 和 "iPad 13 英寸"——
    # 两者看起来都是「Apple 设备,13」。
    #
    # 修复方案具备产品线感知:如果用户明确点名了产品线(iPhone / iPad /
    # MacBook / AirPods / Watch),就要求该 token 出现在结果标题里。
    # 软失败:若过滤会清空列表,则保留原始重排结果(绝不让用户两手空空)。
    # 产品线锚点过滤只用于单产品线查找("iPhone13" → 不要 iPad)。在对比
    # ("iPhone和小米哪个好")或任何点名 ≥2 个品牌的查询里,它会错误地剥掉
    # 另一个品牌 → "目录里没有小米"。这种情况下跳过它;
    # 重排器已经能把两者都排上来(在 145 条目索引上实测验证过)。
    _is_comparison = bool(_COMPARISON_RE.search(text))
    _multi_brand = bool(retrieval_filter) and _distinct_brand_count(getattr(retrieval_filter, "brand_include", None)) >= 2
    if not (_is_comparison or _multi_brand):
        candidates = _filter_by_product_line(text, candidates)

    # 5) 检索后复查硬约束。海外货源商品到这一步才拿到实时折算的人民币价,
    # 所以人民币预算从现在起才变成严格约束。
    if retrieval_filter:
        from rag.retrieve.query import apply_product_filter
        if has_price_filter:
            from app.services.currency import normalize_product_prices

            candidates = normalize_product_prices(candidates)
        candidates = apply_product_filter(candidates, retrieval_filter, strict_cny_price=True)

    # 6) 价格意图是排在硬约束之后的偏好层。
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

    # 按实体保障覆盖:无论是对比、还是裸的多品牌列表("iphone华为小米呢"),
    # 都应让每个被点名的品牌留在 top-5 里,避免重排器在一个上重复下注
    # 而挤掉另一个(之前在这里丢过 iPhone)。
    if (_is_comparison or _multi_brand) and retrieval_filter and (getattr(retrieval_filter, "brand_include", None) or []):
        _anchors = tuple(a for a in _PRODUCT_LINE_ANCHORS if a in text.lower())
        candidates = _ensure_brand_coverage(candidates, retrieval_filter.brand_include, anchors=_anchors, top=5)

    return candidates


def detect_topic_switch(conversation_filter, raw_message_for_anchor: str) -> bool:
    """top_k 的话题切换检测(路径 A 产品线锚点 + 路径 B 类目/品牌/细分品类冲突)。

    从 top_k 里原样抽出来,供智能体路径复用同一口径:智能体的会话硬约束
    (预算 / 排除)也必须在用户换话题时丢掉,否则"500 元以内的耳机"之后问
    "iPhone 和小米哪个好",智能体会带着 ¥500 上限去搜手机。返回 True 表示换话题。
    """
    from rag.retrieve.constraints import build_retrieval_filter
    from rag.retrieve.query import Filter

    topic_switch = False
    # 路径 A:显式的产品线锚点。
    if raw_message_for_anchor and any(
        a in raw_message_for_anchor.lower() for a in _PRODUCT_LINE_ANCHORS
    ):
        topic_switch = True

    # 路径 B(R8.F.8.1,扩充版):对继承的过滤器在类目或品牌任一维度做
    # 冲突检查。早期版本只检查类目,导致继承的 brand_include = ["Apple"]
    # (来自之前的 iPad 轮)即使新查询带有清晰的类目信号也继续过滤检索
    # ——这就是「iPad 轮之后 护肤品 / 鞋子 / 纸尿片 返回 0 条结果」
    # 那次故障。
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

        # 类目冲突:原始消息带有与继承不一致的新类目信号
        # (也包括「继承里本来没有类目,但品牌之类的其它条件
        # 还黏着」的情况)。
        cat_conflict = bool(raw_cat and raw_cat != inh_cat)

        # 品牌冲突:原始消息有 brand_include 且与继承的不相交。
        # 例如 iPad 轮之后说 "推荐 OPPO 手机",而 conversation_filter
        # 继承了 brand_include = ["Apple"],就会触发。
        brand_conflict = bool(raw_brands and inh_brands and not (raw_brands & inh_brands))

        # 另外:原始消息有类目,而继承里带着另一个生态的 brand_include
        # (典型场景:iPad 轮留下 brand=Apple,然后用户说 "护肤品"
        # ——类目不同,品牌也不同)。
        category_vs_brand_conflict = bool(
            raw_cat and inh_brands and not raw_brands and raw_cat != inh_cat
        )

        # R9.A.1 —— 路径 C:sub_categories 冲突。
        # 按 Sam 的 CONTEXT_CONTAMINATION_DIAGNOSIS.md,这是泄漏最严重的维度。
        # 早前轮次继承下来的 sub_categories(例如 "推荐适合敏感肌的洁面"
        # 留下的 ['洁面'])会在不相关的轮次(iPad / 鞋子 / 纸尿片)中一路
        # 存活,因为这些轮次都不产生类目信号,也都没提到 洁面。到第 5 轮,
        # 最终查询 "护肤品" 与继承的类目(美妆护肤)匹配,所以上面的
        # cat_conflict 不会触发——但 sub_categories=['洁面'] 还在,
        # 把检索收窄到单一商品类。
        #
        # 检测条件:继承里有 sub_categories,且
        #   (a) 当前原始轮提取出了自己的 sub_categories,且与继承的
        #       不相交,或
        #   (b) 当前轮没有产出 sub_categories,且其文本没提到任何继承的
        #       sub_category token,且它带有新的话题信号(类目或品牌)。
        # 情况 (b) 把 "iPad" / "护肤品" 等当成话题切换,
        # 同时不会在 "再便宜点的" 这类追问上误报
        # (它们自身没有类目/品牌信号)。
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
                # 情况 (a):双方都有 sub_cats——没有交集即为冲突。
                sub_conflict = not (set(raw_sub_cats) & set(inh_sub_cats))
            elif not text_mentions_inherited and raw_cat:
                # 情况 (b):继承里有过期的 sub_cats,当前轮带有新的
                # 类目信号、却没有引用任何继承的 sub_cat
                # → 话题切换。
                sub_conflict = True
            elif not text_mentions_inherited and raw_brands:
                # 情况 (c)—— R13 修复:只点了品牌的轮次(推荐笔记本 之后
                # 说 "苹果的呢"),当该品牌在继承的货架上有商品在售时,
                # 其实是同一购物需求的细化;把它当成切换会丢掉整个对话
                # 过滤器,连类目一起丢失。只有当被点名的品牌在继承类目下
                # 没有任何商品时(例如 洁面 之后说 OPPO)
                # 才视为切换。
                inh_cat_for_brands = conversation_filter.category
                if inh_cat_for_brands:
                    try:
                        from rag.retrieve.constraints import _catalog_brand_cats

                        bcats = _catalog_brand_cats()
                        sub_conflict = not any(
                            inh_cat_for_brands in bcats.get(str(b).casefold(), frozenset())
                            for b in raw_brands
                        )
                    except Exception:
                        sub_conflict = True
                else:
                    sub_conflict = True

        if cat_conflict or brand_conflict or category_vs_brand_conflict or sub_conflict:
            topic_switch = True

    return topic_switch


def top_k(
    text: str,
    k: int = 5,
    filters: dict | None = None,
    *,
    conversation_filter=None,
    intent_text: str | None = None,
    user_id: str | None = None,
    relevance_gate: bool = True,
    skip_topic_switch: bool = False,
) -> list[dict]:
    """混合检索 + (可选)改写 + 否定过滤 + 重排序 → top-k 商品。

    R9.B:给定 `user_id` 时,用一个温和的偏好先验(来自用户的 👍/👎
    历史)在截断前对最终列表重新排序。

    `skip_topic_switch`(内部参数,只给多跳 hop2 / 智能体工具用):调用方传入的
    conversation_filter 是**程序派生**的权威约束(锚点价格/品牌/品类),不是从
    历史对话继承来的,因此不能被下面的话题切换检测丢掉。默认 False,单跳行为不变。
    """
    synonyms_on = os.getenv("RAG_SYNONYMS", "1") == "1"
    rewrite_on = os.getenv("RAG_REWRITE", "0") == "1"
    rerank_on = os.getenv("RAG_RERANK", "1") == "1"
    negation_on = (os.getenv("RAG_NEGATION", "1") == "1") and _negation_signals(text)
    price_on = os.getenv("RAG_PRICE_INTENT", "1") == "1"
    hard_filters_on = os.getenv("RAG_HARD_FILTERS", "1") == "1"

    from rag.retrieve.constraints import build_retrieval_filter
    from rag.retrieve.query import Filter

    # R8.F.7 —— 话题切换检测(R8.F.8 中做了泛化)。
    #
    # 最初的窄版本只能捕获 Apple 产品线锚点(iPhone / iPad / MacBook /
    # AirPods / ...)。用户反馈(以及「护肤之后接零食」那次回归)表明
    # 这是在打地鼠:切换到 "我想买点零食" 或 "Nike 跑鞋" 时,继承下来的
    # 美妆护肤 过滤器仍会让检索颗粒无收。
    #
    # 泛化为两个互补信号——任一命中都触发切换:
    #
    #   路径 A  硬编码的产品线锚点(iPhone / iPad 等)。这些 token 是
    #           SKU 产品线名,build_retrieval_filter 不知道怎么把它们
    #           映射到类目。保留这份显式列表当安全网。
    #
    #   路径 B  从当前用户的原始消息(intent_text,而不是经过上下文改写
    #           的文本)重新提取一个 Filter。如果它携带的 category /
    #           sub_category / brand_include 信号与继承的
    #           conversation_filter 不同,说明用户明确点了新话题——重置。
    #
    # 任一路径触发都会丢弃 conversation_filter,并用原始消息替换改写后
    # 的文本。"再便宜点的" 这类追问(自身没有类目/品牌信号)
    # 仍然正常继承。
    raw_message_for_anchor = intent_text or text or ""
    # 多跳 hop2 的派生 Filter 没有 category(只有价格/品牌/细分品类),而 hop2 的
    # intent_text 是目标品类词("降噪耳机" → 数码电子),路径 B 必然判成类目冲突、
    # 把派生约束整个丢掉(多跳 Bug 2)。派生约束是权威的,跳过检测。
    # 检测本体见 detect_topic_switch(路径 A / B 的完整说明在那里)。
    topic_switch = (not skip_topic_switch) and detect_topic_switch(
        conversation_filter, raw_message_for_anchor)

    if topic_switch:
        conversation_filter = None
        text = raw_message_for_anchor  # 绕过上下文查询改写器
        # 原始消息丢掉了 chat.py 做的英文增强——重新应用一次,
        # 让英文的话题切换仍然带着对应的中文类目提示。
        try:
            from rag.retrieve.english_terms import augment_english_query

            text = augment_english_query(text)
        except Exception:
            pass

    if hard_filters_on and isinstance(conversation_filter, Filter):
        # 对话状态是权威的——包括用户明确取消了早前轮次继承的条件之后
        # 留下的空 Filter。
        retrieval_filter = conversation_filter
    else:
        retrieval_filter = build_retrieval_filter(text if hard_filters_on else "", filters)
    preference_text = intent_text if intent_text is not None else text

    # R10 方案 A —— 检索结果缓存。第 1-6 步(查询扩展 → 混合检索 →
    # 否定过滤 → 重排序 → 锚点过滤 → 硬约束 → 价格意图)既昂贵又与偏好
    # 无关,所以做 memoize。命中时完全跳过混合检索 + v2-m3 交叉编码器
    # ——那是延迟的大头,英文链路尤甚。廉价、用户相关的偏好重排
    # (第 7 步)留在下面、缓存之外,
    # 这样 👍/👎 仍能实时调整顺序,提案 #12 也得以保留。
    _rc_key = _retrieval_cache_key(
        text, k, retrieval_filter, f"{preference_text}|gate={relevance_gate}"
    )
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
            relevance_gate=relevance_gate,
        )
        _retrieval_cache_put(_rc_key, candidates)

    # 7) R9.B —— 闭环偏好先验(提案 #12)。按用户的 👍/👎 历史做温和、
    # 有界的重排。用户没有偏好记录时等于不操作。放在最后一步应用,
    # 此时相关性 + 硬约束都已尘埃落定;
    # 偏好只在几乎打平的候选之间轻推一下。
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
    """用 CLIP 找视觉相似的 top-k。CLIP 不可用时返回空列表。

    这是 IMAGE_TEXT_FUSION=0(或融合路径出异常)时的旧行为:只看第一张图、
    不看文字、没有相关性下限。新路径见 image_text_retrieve。"""
    try:
        from rag.retrieve.query import query_image
        hits = query_image(image_bytes, k=k)
        return [h.product for h in hits]
    except Exception:
        return []


def image_text_retrieve(
    images: list[bytes],
    text: str,
    *,
    turn_filter=None,
    conversation_filter=None,
    user_id: str | None = None,
    k: int | None = None,
):
    """拍照找货 + 文字(IMAGE_TEXT_FUSION=1)。全程不调 LLM。

    1. 多图 CLIP 召回(每张 top-IMAGE_RECALL_N,按商品取最大相似度);
    2. rag.retrieve.image_fusion 做相关性下限 / 硬约束 / 文字融合排序;
    3. 约束把视觉候选全部筛掉时,在锚点(视觉最相似商品)的品类里按同一组约束
       走文字检索(top_k,skip_topic_switch——回退 Filter 是程序派生的权威约束),
       细分品类里没有就放宽到大类再试一次;结果再过一遍严格的本地否定规则。

    返回 ImageFusionResult。status="below_floor" / "no_visual" 时 products 为空,
    由 chat.py 决定走普通文字检索还是如实说没有相似商品。异常向上抛,
    由 chat.py 捕获后回退旧行为。
    """
    from rag.retrieve import image_fusion as F
    from rag.retrieve.query import query_images

    hits = query_images(list(images)[: F.max_query_images()], k=F.recall_n())
    return image_text_fuse_hits(
        hits, text,
        turn_filter=turn_filter, conversation_filter=conversation_filter, user_id=user_id, k=k,
    )


def image_text_fuse_hits(
    hits,
    text: str,
    *,
    turn_filter=None,
    conversation_filter=None,
    user_id: str | None = None,
    k: int | None = None,
    rerank_fn=None,
):
    """image_text_retrieve 的后半段:给定已召回的视觉命中,做融合 + 约束清空时的文字回退。
    拆出来是为了让离线评测(rag/eval/image_text_eval.py)复用同一份代码,
    而不必为每个文字变体重复跑 CLIP。"""
    from rag.retrieve import image_fusion as F
    from rag.retrieve.query import Filter
    from app.services.currency import normalize_product_prices

    k = k or F.image_top_k()
    res = F.fuse_image_candidates(
        hits, text,
        turn_filter=turn_filter,
        conversation_filter=conversation_filter,
        k=k,
        normalize_fn=normalize_product_prices,
        rerank_fn=rerank_fn,
    )
    if res.status != "constraints_emptied" or res.fallback_filter is None:
        return res

    def _search(flt) -> list[dict]:
        found = top_k(
            res.fallback_query or "",
            k=max(k, 5),
            conversation_filter=flt,
            intent_text=res.fallback_intent,
            user_id=user_id,
            skip_topic_switch=True,
        )
        found = normalize_product_prices(found)
        # 兜底检索层里 国产 / X以外 是 fail-soft 的,这里再严格过一遍,保证卡片不违反约束
        found = F.apply_local_negation(found, res.negation)
        return F.apply_constraints(found, flt, F.LocalNegation())

    fb = res.fallback_filter
    products = _search(fb)
    res.trace["fallback"] = "sub_category"
    if not products and (fb.sub_categories or fb.sub_category) and fb.category:
        wider = Filter(**{name: getattr(fb, name) for name in fb.__dataclass_fields__})
        wider.sub_categories = None
        wider.sub_category = None
        products = _search(wider)
        res.trace["fallback"] = "category"
    res.products = products[:k]
    return res


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


# ---------------------------------------------------------------------------
# Multi-hop 检索(锚定式两跳)。见 rag/retrieve/multihop.py 的设计说明。
# 每一跳都复用上面的 top_k(混合检索+重排+否定/预算过滤+缓存),
# 跳间只传播**结构化属性**(价格/品牌/品类),不经过 LLM → 可断言、零幻觉。
# ---------------------------------------------------------------------------
def multi_hop_retrieve(
    plan,
    *,
    history_products: list[dict] | None = None,
    user_id: str | None = None,
    k: int = 4,
) -> tuple[dict | None, list[dict], dict]:
    """执行两跳检索。

    返回 (anchor_product, hop2_products, trace)。
    - anchor_product 为 None 表示 hop1 没锚定到商品 → 调用方应回退单跳。
    - trace 是给 SSE `hop_trace` 事件用的可解释检索链。
    """
    from rag.retrieve.multihop import (
        anchor_attrs, derive_filter, relation_label, RELATION_PAIR, PAIR_MAP,
    )

    trace: dict = {"relation": plan.relation, "hops": []}

    # ---- hop 1:锚定 ----
    anchor: dict | None = None
    if plan.uses_history_anchor:
        # 会话锚点:直接引用上一轮的商品卡,不检索
        cards = history_products or []
        if cards:
            idx = len(cards) - 1 if plan.anchor_ordinal == -1 else plan.anchor_ordinal - 1
            if 0 <= idx < len(cards):
                anchor = cards[idx]
        trace["hops"].append({
            "hop": 1, "kind": "history_anchor",
            "query": f"上一轮第{plan.anchor_ordinal}款",
            "hit": (anchor or {}).get("title"),
        })
    else:
        hits = top_k(plan.anchor_text, k=3, intent_text=plan.anchor_text,
                     user_id=user_id, relevance_gate=False)
        anchor = hits[0] if hits else None
        trace["hops"].append({
            "hop": 1, "kind": "retrieval", "query": plan.anchor_text,
            "hit": (anchor or {}).get("title"),
        })

    if not anchor:
        trace["fallback"] = "anchor_not_found"
        return None, [], trace

    # Bug 1 修复:锚点先做汇率归一化,保证 anchor_attrs 拿到的是**人民币**价。
    # 外币锚点(海外版 AirPods Pro 2,base_price=249 USD)不归一化就会被当成 ¥249。
    from app.services.currency import normalize_product_price
    anchor = normalize_product_price(anchor)
    attrs = anchor_attrs(anchor)
    trace["anchor"] = {
        "product_id": attrs.get("product_id"), "title": attrs.get("title"),
        "brand": attrs.get("brand"), "price_cny": attrs.get("price_cny"),
        "sub_category": attrs.get("sub_category"),
    }
    # 价格类关系需要锚点人民币价;汇率不可用(外币且无旧报价)时没法做正确比较,
    # 宁可放弃多跳、回退单跳,也不拿外币数字硬比。
    if plan.relation in _PRICE_RELATIONS and attrs.get("price_cny") is None:
        trace["fallback"] = "anchor_price_unknown"
        return anchor, [], trace

    # ---- 目标品类:显式 target 优先;否则按关系推断 ----
    target_text = (plan.target_text or "").strip()
    target_subs = None
    if plan.relation == RELATION_PAIR and not target_text:
        target_subs = list(PAIR_MAP.get(attrs.get("sub_category") or "", ()))
        target_text = target_subs[0] if target_subs else ""
    if not target_text:
        # "比X便宜的" 没说品类 → 沿用锚点自己的品类
        target_text = attrs.get("sub_category") or attrs.get("category") or plan.anchor_text
    # 目标词退化成锚点词本身时(锚点没有品类信息),不能把它的品牌信号并进 hop2
    # ("比 AirPods 便宜的" ≠ "只要 Apple")。
    target_is_anchor_text = target_text == plan.anchor_text

    hop2_filter = derive_filter(attrs, plan.relation, target_sub_categories=target_subs)
    # 放宽品类:派生 filter 里的 sub_categories 可能过窄(如"真无线降噪耳机"
    # 只有锚点自己),用兄弟品类一起召回,再由下面的断言把关。
    if hop2_filter is not None and getattr(hop2_filter, "sub_categories", None):
        hop2_filter.sub_categories = _sibling_sub_categories(hop2_filter.sub_categories)
    hop2_filter = _merge_hop2_filter(
        hop2_filter, "" if target_is_anchor_text else target_text,
        prefer_target_subs=plan.relation in _PRICE_RELATIONS and bool((plan.target_text or "").strip()),
    )
    trace["derived_filter"] = {
        "price_max_cny": getattr(hop2_filter, "price_max_cny", None) if hop2_filter else None,
        "price_min_cny": getattr(hop2_filter, "price_min_cny", None) if hop2_filter else None,
        "brand_include": getattr(hop2_filter, "brand_include", None) if hop2_filter else None,
        "sub_categories": getattr(hop2_filter, "sub_categories", None) if hop2_filter else None,
    }

    # ---- hop 2:带派生约束检索 ----
    # Bug 2 修复:skip_topic_switch=True。派生 Filter 不带 category,而 intent_text
    # 是目标品类词,top_k 的话题切换检测必然判成"换话题"并把 conversation_filter
    # 置空——派生的价格/品牌约束根本到不了 _heavy_retrieve,只能靠下面的断言事后剔除,
    # 召回变少、频繁掉进 relaxed 兜底。
    results = top_k(target_text, k=k + 8, conversation_filter=hop2_filter,
                    intent_text=target_text, user_id=user_id, relevance_gate=False,
                    skip_topic_switch=True)
    # Bug 1 修复:hop2 候选也统一归一化成人民币,断言/排序只比较 CNY。
    from app.services.currency import normalize_product_prices
    results = normalize_product_prices(results)
    # 排除锚点自身(product_id 优先;缺失时用标题兜底,避免锚点重复出现在结果里)
    anchor_id = attrs.get("product_id")
    anchor_title = (attrs.get("title") or "").strip()

    def _is_anchor(p: dict) -> bool:
        pid = p.get("product_id")
        if anchor_id and pid:
            return pid == anchor_id
        return bool(anchor_title) and (p.get("title") or "").strip() == anchor_title

    results = [p for p in results if not _is_anchor(p)]

    # **约束断言**(纵深防御,不是"top_k 会 fail-soft 放行"——_heavy_retrieve 第 5 步
    # 对价格/品牌/品类是严格过滤,不会在结果为空时放行)。它真正防的是:
    #   1) 派生约束没能进到检索:top_k 的话题切换检测曾把 hop2 Filter 整个丢掉
    #      (Bug 2,现已用 skip_topic_switch 修复),RAG_HARD_FILTERS=0 时也会忽略它;
    #   2) 价格口径:断言统一用人民币(price_in_cny),外币候选不会因汇率缺失或
    #      未归一化而拿外币数字混进来(Bug 1);
    #   3) 检索链路以后的任何改动(缓存、兜底、新过滤层)都不会让"比X便宜"悄悄
    #      返回更贵的商品——这条不变量只在这里程序化地钉死一次。
    # relation_correctness 指标衡量的正是这一层的输出。
    results = _assert_relation(results, attrs, plan.relation, hop2_filter)
    results = results[:k]

    # 约束下确实没有 → 放宽兜底:同品类里取最接近的,并在 trace 标记,
    # prompt 会据此如实说明"没有完全符合的,最接近的是…"(与单跳预算兜底同策略)
    relaxed = False
    if not results:
        widened = top_k(target_text, k=k + 4, intent_text=target_text,
                        user_id=user_id, relevance_gate=False)
        widened = [p for p in widened if not _is_anchor(p)]
        anchor_price = attrs.get("price_cny")
        if anchor_price and plan.relation in _PRICE_RELATIONS:
            # Bug 1 修复:按人民币距离排序(外币候选先归一化),不再拿外币 base_price 比
            widened = normalize_product_prices(widened)

            def _pv(p):
                from app.services.currency import price_in_cny
                v = price_in_cny(p, fetch=False)   # widened 刚归一化过
                return v if v is not None else float("inf")
            widened.sort(key=lambda p: abs(_pv(p) - anchor_price))
        results = widened[:k]
        relaxed = bool(results)

    trace["hops"].append({
        "hop": 2, "kind": "retrieval", "query": target_text,
        "count": len(results), "relaxed": relaxed,
    })
    trace["label"] = relation_label(plan.relation, attrs)
    trace["relaxed"] = relaxed
    if not results:
        trace["fallback"] = "hop2_empty"
    return anchor, results, trace


def _sibling_sub_categories(subs: list[str]) -> list[str]:
    """把过窄的细分品类扩成同族兄弟(耳机族/跑鞋族等),避免 hop2 召回为空。"""
    FAMILIES = (
        {"无线降噪耳机", "真无线耳机", "真无线降噪耳机"},
        {"跑步鞋", "运动休闲鞋"},
        {"徒步鞋", "登山徒步鞋"},
        {"面霜", "面霜/敏感肌"},
        {"精华", "精华液", "精华水"},
        {"化妆水", "化妆水/精华水", "爽肤水/化妆水"},
        {"短袖T恤", "速干T恤"},
        {"运动长裤", "运动短裤"},
    )
    out = set(subs)
    for fam in FAMILIES:
        if out & fam:
            out |= fam
    return sorted(out)


_PRICE_RELATIONS = ("cheaper", "pricier", "same_price")


def _expand_brand_aliases_in_catalog(brands: list[str]) -> list[str]:
    """把品牌扩成目录里所有同簇写法("Apple 苹果" → + "Apple" / "苹果")。

    product_matches_filter 的 brand_include 是精确匹配(casefold),目录里同一品牌
    存在多种写法,只用锚点自己的写法会漏掉同品牌其他商品("和 Apple 一样牌子的
    笔记本"锚到 "Apple 苹果" 后找不到品牌写成 "Apple" 的 MacBook)。"""
    out = list(dict.fromkeys(b for b in brands if b))
    try:
        from rag.retrieve.constraints import _catalog_brands
        wanted: set[str] = set()
        for b in out:
            wanted |= _brand_match_terms(b)
        for cb in _catalog_brands():
            if cb not in out and (_brand_match_terms(cb) & wanted):
                out.append(cb)
    except Exception:
        pass
    return out


def _merge_hop2_filter(derived, target_text: str, *, prefer_target_subs: bool = False):
    """派生约束 ∧ 目标词约束 → hop2 的权威 Filter。

    hop2 跳过了话题切换检测(派生约束不许被丢),那么目标词本身的品类信号
    ("和 iPhone 一样牌子的**平板**")也得显式并进来,否则只剩 brand=Apple,
    会把 iPhone/MacBook 一起召回。规则:
      * 价格 / 品牌以派生为准(只会更严),目标词里的排除条件照搬;
      * `prefer_target_subs`(价格类关系 + 用户点名了目标品类):品类以目标词为准。
        derive_filter 给价格类关系填的是**锚点自己的**品类,那只是"没说品类时"的
        默认值;用户说了"跟神仙水同价位的**化妆水**"就该找化妆水,而不是锚点在
        目录里被标成的"精华水";
      * 否则两边都有时取交集(不相交以目标词为准);只有一边有就用那一边;
        都没有细分品类时沿用目标词的 category。
    目标词的细分品类也按同族兄弟扩展,与派生一侧口径一致。
    """
    from rag.retrieve.query import Filter
    try:
        from rag.retrieve.constraints import build_retrieval_filter
        text_f = build_retrieval_filter(target_text or "", None)
    except Exception:
        text_f = None
    if derived is None and text_f is None:
        return None
    d = derived or Filter()
    t = text_f or Filter()
    merged = Filter(
        price_max_cny=d.price_max_cny if d.price_max_cny is not None else t.effective_price_max_cny,
        price_min_cny=d.price_min_cny if d.price_min_cny is not None else t.effective_price_min_cny,
        brand_exclude=t.brand_exclude,
        exclude_keywords=t.exclude_keywords,
    )
    if d.brand_include:
        merged.brand_include = _expand_brand_aliases_in_catalog(list(d.brand_include))
    elif t.brand_include:
        merged.brand_include = list(t.brand_include)
    d_subs = list(d.sub_categories or ([d.sub_category] if d.sub_category else []))
    t_subs = list(t.sub_categories or ([t.sub_category] if t.sub_category else []))
    if t_subs:
        t_subs = _sibling_sub_categories(t_subs)
    if prefer_target_subs and (t_subs or t.category):
        if t_subs:
            merged.sub_categories = t_subs
        else:
            merged.category = t.category
    elif d_subs and t_subs:
        inter = [s for s in d_subs if s in set(t_subs)]
        merged.sub_categories = inter or t_subs
    elif d_subs:
        merged.sub_categories = d_subs
    elif t_subs:
        merged.sub_categories = t_subs
    elif t.category:
        merged.category = t.category
    return merged if merged.active else None


def _assert_relation(products: list[dict], attrs: dict, relation: str, hop2_filter) -> list[dict]:
    """确定性校验 hop2 结果是否真的满足派生约束。不满足的剔除。

    价格一律按人民币比较(price_in_cny:已归一化的 price_cny / CNY 商品的
    base_price / 外币按参考汇率换算);拿不到人民币价的候选在有价格约束时剔除。
    """
    if not products:
        return products
    from app.services.currency import price_in_cny

    price_max = getattr(hop2_filter, "price_max_cny", None) if hop2_filter else None
    price_min = getattr(hop2_filter, "price_min_cny", None) if hop2_filter else None
    brands = getattr(hop2_filter, "brand_include", None) if hop2_filter else None

    out = []
    for p in products:
        pr = price_in_cny(p, fetch=False)   # 候选已在 multi_hop_retrieve 里归一化
        if price_max is not None and (pr is None or pr > price_max):
            continue
        if price_min is not None and (pr is None or pr < price_min):
            continue
        if brands:
            b = (p.get("brand") or "").casefold()
            if not any((x or "").casefold() in b or b in (x or "").casefold() for x in brands):
                continue
        out.append(p)
    return out
