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


def top_k(
    text: str,
    k: int = 5,
    filters: dict | None = None,
    *,
    conversation_filter=None,
    intent_text: str | None = None,
    user_id: str | None = None,
    relevance_gate: bool = True,
) -> list[dict]:
    """混合检索 + (可选)改写 + 否定过滤 + 重排序 → top-k 商品。

    R9.B:给定 `user_id` 时,用一个温和的偏好先验(来自用户的 👍/👎
    历史)在截断前对最终列表重新排序。
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
    """用 CLIP 找视觉相似的 top-k。CLIP 不可用时返回空列表。"""
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
