"""Warm the lazy retrieval models before the backend accepts chat requests.

P0.4 —— 另有向量库就绪门控 ``vector_store_gate()``:绕开 ``rag.retrieve.query`` 里的
稠密→关键词兜底,直接查**当前配置的**底层存储(影子 / 兜底包装都剥掉),要求:

* text / image 两个集合条数 > 0(下限可用 ``RAG_READY_MIN_TEXT`` / ``RAG_READY_MIN_IMAGE`` 调高);
* 用真实的查询向量查 text 集合、用库里一条真实的图片向量查 image 集合,结果都非空;
* Milvus 额外要求两个集合都是 schema v2(v2 标量字段齐全)。Chroma 的旧索引
  (``schema_version`` 为 null,线上现在就是)照样放行——门控查的是"能不能用",
  不是"是不是最新"。

为什么需要它:原来 ``/ready`` 只看模型是否预热完。向量库挂了,预热里的那次检索会被
关键词兜底接住,``/ready`` 照样 200,autodeploy 的回滚判断等于没有。

开关 ``RAG_READY_GATE``:

* ``off``(默认)—— 不跑门控,``/ready`` 与之前完全一致(只多一个降级计数字段);
* ``report`` —— 跑门控并在 ``/ready`` 的 JSON 里展示结果,状态码不变。上线前先用它
  确认生产索引能过门控(线上 Chroma 旧索引应当 ok=true);
* ``enforce`` —— 门控不过时 ``/ready`` 返回 503。``tools/cloud-autodeploy.sh`` 用的是
  ``curl -sf … | grep ready``,非 2xx 时 curl 不输出任何内容,部署即判失败并回滚。
  生产由 ``deploy/systemd/lionpick.service.d/05-ready-gate.conf`` 打开。

默认取 off 而不是 report:report 也会在第一次 ``/ready`` 时加载嵌入模型、查两次库;
对没预热嵌入模型的进程(单测里的 TestClient、诊断用的 ``RAG_PREWARM=0``)这是新增开销,
而且超时后后台线程还在跑,会拖住进程退出。

门控结果缓存 ``RAG_READY_GATE_TTL_S`` 秒(默认 30),同一时刻最多只有一次门控在跑,
``/ready`` 被高频探测也不会把检索压垮。门控失败**不**拦截聊天:主存储挂了时用户路径
仍可由限期兜底 / 关键词检索接住,门控只负责让探活和回滚"看得见"。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def warm_retrieval_pipeline() -> dict[str, str]:
    """Load retrieval-time models and caches synchronously during startup."""
    if os.getenv("RAG_PREWARM", "1") != "1":
        return {"prewarm": "disabled", "embedding": "lazy", "bm25": "lazy", "reranker": "lazy"}

    from rag.ingest.embed_text import embed_query
    from rag.retrieve.bm25 import bm25_topk

    sample_query = "推荐一款日常洁面产品"
    embed_query(sample_query)
    bm25_topk(sample_query, k=1)

    reranker = "disabled"
    if os.getenv("RAG_RERANK", "1") == "1":
        from rag.retrieve.rerank import warmup_reranker

        warmup_reranker()
        reranker = "ready"

    # Exercise Chroma, hybrid fusion and a realistic candidate rerank before
    # readiness, so the first user request does not initialize that path.
    from app.services.rag_client import top_k

    top_k("推荐适合日常使用的商品", k=5)

    # R11.fix — preload CLIP (the image→image retriever) too. It loads a
    # ~600 MB OpenCLIP model lazily on the first 拍照找货 (~37 s cold), so a
    # demo-day restart would make the first photo query look hung. Warm it by
    # running one real image query against a seed product image.
    clip = "disabled"
    if os.getenv("RAG_PREWARM_CLIP", "1") == "1":
        try:
            import glob

            from rag.retrieve.query import query_image

            imgs = glob.glob(str(REPO_ROOT / "data" / "seed" / "**" / "images" / "*.jpg"), recursive=True)
            if imgs:
                with open(imgs[0], "rb") as f:
                    query_image(f.read(), k=1)
                clip = "ready"
        except Exception:
            clip = "error"

    # R15 — report which vector store is serving and what its index holds
    # (backend, schema version, filterable fields, doc counts). An index built
    # by older ingest code shows schema_version=null here, which is exactly the
    # silent local-vs-cloud drift we had before this field existed.
    try:
        from rag.store import describe_store

        vector_store = describe_store()
    except Exception as exc:
        vector_store = {"error": str(exc)}

    detail = {
        "prewarm": "completed",
        "embedding": "ready",
        "bm25": "ready",
        "reranker": reranker,
        "clip": clip,
        "query_path": "ready",
        "vector_store": vector_store,
    }
    # 强制模式下启动时先跑一次门控,填好缓存:第一次 /ready 就有结果,不用等。
    if gate_mode() == "enforce":
        try:
            detail["vector_store_gate_at_startup"] = bool(gate_status(force=True).get("ok"))
        except Exception:
            detail["vector_store_gate_at_startup"] = False
    return detail


# ---------------------------------------------------------------------------
# 向量库就绪门控(P0.4)
# ---------------------------------------------------------------------------

GATE_MODES = ("off", "report", "enforce")
GATE_QUERY = "推荐一款日常洁面产品"

_gate_lock = threading.Lock()
_gate_cache: dict = {"at": 0.0, "result": None}
_gate_vec: dict = {"text": None}


def gate_mode() -> str:
    mode = (os.getenv("RAG_READY_GATE") or "off").strip().lower()
    return mode if mode in GATE_MODES else "off"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _gate_query_vector(embed=None):
    if embed is not None:
        return embed(GATE_QUERY)
    vec = _gate_vec["text"]
    if vec is None:
        from rag.ingest.embed_text import embed_query

        vec = embed_query(GATE_QUERY)
        _gate_vec["text"] = vec
    return vec


def _probe_image_vector(store):
    """取库里的一条真实图片向量当查询(不用再跑一遍 CLIP,结果至少应命中它自己)。"""
    from rag.store import IMAGE_COLLECTION

    gen = store.export(IMAGE_COLLECTION, batch_size=1)
    try:
        first = next(iter(gen), None)
    finally:
        close = getattr(gen, "close", None)
        if close:
            close()
    if not first or not first[1]:
        return None
    return first[1][0]


def vector_store_gate(store=None, *, embed=None) -> dict:
    """直接查底层向量库,判断它能不能真正提供稠密检索。从不抛异常。"""
    t0 = time.perf_counter()
    reasons: list[str] = []
    checks: dict = {}
    result: dict = {"ok": False, "backend": None, "checks": checks, "reasons": reasons}
    try:
        from rag.store import SCHEMA_VERSION, get_store
        from rag.store.base import IMAGE_SCALAR_FIELDS, TEXT_SCALAR_FIELDS
        from rag.store.composite import unwrap

        st = store if store is not None else unwrap(get_store())
        result["backend"] = st.backend
    except Exception as exc:
        reasons.append(f"store unavailable: {type(exc).__name__}: {exc}")
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result

    require_image = os.getenv("RAG_READY_GATE_REQUIRE_IMAGE", "1") != "0"
    min_text = max(1, _env_int("RAG_READY_MIN_TEXT", 1))
    min_image = max(1, _env_int("RAG_READY_MIN_IMAGE", 1))

    try:
        info = st.schema_info()
        result["store"] = info
    except Exception as exc:
        info = {}
        reasons.append(f"schema_info failed: {type(exc).__name__}: {exc}")

    # -- text ---------------------------------------------------------------
    try:
        n = int(st.text_count())
        checks["text_count"] = n
        if n < min_text:
            reasons.append(f"text collection has {n} docs (< {min_text})")
    except Exception as exc:
        reasons.append(f"text count failed: {type(exc).__name__}: {exc}")
    try:
        hits = st.query_text(_gate_query_vector(embed), 5)
        checks["text_hits"] = len(hits)
        if not hits:
            reasons.append("text query returned no hits")
    except Exception as exc:
        reasons.append(f"text query failed: {type(exc).__name__}: {exc}")

    # -- image --------------------------------------------------------------
    if require_image:
        try:
            n = int(st.image_count())
            checks["image_count"] = n
            if n < min_image:
                reasons.append(f"image collection has {n} vectors (< {min_image})")
        except Exception as exc:
            reasons.append(f"image count failed: {type(exc).__name__}: {exc}")
        try:
            vec = _probe_image_vector(st)
            if vec is None:
                reasons.append("image collection: no vector to probe with")
            else:
                hits = st.query_image(vec, 3)
                checks["image_hits"] = len(hits)
                if not hits:
                    reasons.append("image query returned no hits")
        except Exception as exc:
            reasons.append(f"image query failed: {type(exc).__name__}: {exc}")

    # -- schema(仅 Milvus:显式 schema,v2 字段缺了查询侧会误判能下推)--------
    if st.backend == "milvus":
        labels = [("text", TEXT_SCALAR_FIELDS)] + ([("image", IMAGE_SCALAR_FIELDS)] if require_image else [])
        for label, declared in labels:
            entry = info.get(label) if isinstance(info, dict) else None
            if not isinstance(entry, dict):
                reasons.append(f"{label}: no schema info")
                continue
            version = entry.get("schema_version")
            checks[f"{label}_schema_version"] = version
            missing = sorted(set(declared) - set(entry.get("filterable_fields") or []))
            if version != SCHEMA_VERSION or missing:
                reasons.append(f"{label}: schema_version={version} missing={missing} (need v{SCHEMA_VERSION})")

    result["ok"] = not reasons
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def gate_status(*, force: bool = False, ttl_s: float | None = None) -> dict:
    """带缓存的门控结果。同一时刻只跑一次;别人正在跑时返回上一次结果(或 pending)。"""
    if ttl_s is None:
        try:
            ttl = float(os.getenv("RAG_READY_GATE_TTL_S", "30"))
        except ValueError:
            ttl = 30.0
    else:
        ttl = ttl_s
    now = time.monotonic()
    cached = _gate_cache["result"]
    if cached is not None and not force and now - _gate_cache["at"] < ttl:
        return {**cached, "age_s": round(now - _gate_cache["at"], 1)}
    if not _gate_lock.acquire(blocking=False):
        if cached is not None:
            return {**cached, "age_s": round(now - _gate_cache["at"], 1), "refreshing": True}
        return {"ok": False, "pending": True, "reasons": ["vector store gate check in progress"]}
    try:
        result = vector_store_gate()
        _gate_cache["result"] = result
        _gate_cache["at"] = time.monotonic()
        return {**result, "age_s": 0.0}
    finally:
        _gate_lock.release()


def reset_gate_cache() -> None:
    """测试用:清空门控缓存。"""
    _gate_cache["result"] = None
    _gate_cache["at"] = 0.0
    _gate_vec["text"] = None


def degradation_stats() -> dict:
    """稠密→关键词兜底次数 + 影子 / 兜底包装计数,供 /ready 展示。从不抛异常。"""
    out: dict = {}
    try:
        from rag.retrieve.query import fallback_stats

        out.update(fallback_stats())
    except Exception as exc:
        out["error"] = str(exc)
    try:
        from rag.store import store_wrapper_stats

        wrappers = store_wrapper_stats()
        if wrappers:
            out["store_wrappers"] = wrappers
    except Exception:
        pass
    return out
