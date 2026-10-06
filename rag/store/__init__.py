"""向量存储层。

后端由环境变量 ``RAG_STORE`` 选择:

* ``chroma``(默认)—— 进程内嵌入式,零依赖,索引在 ``data/.chroma/``;
* ``milvus`` —— ``RAG_MILVUS_URI`` 为文件路径时走 Milvus Lite(默认
  ``data/.milvus/lionpick.db``),为 ``http://host:19530`` 时连 Standalone/集群。
  注意:不要用 ``MILVUS_URI`` 这个名字——pymilvus 自己在 import 时会读它,
  而且要求必须是 http(s) 地址,设成文件路径会让 ``import pymilvus`` 直接报错(R15 实测)。
  本项目的 Milvus 配置统一用 ``RAG_MILVUS_*`` 前缀。

对外保留 R14 之前 ``rag/store.py`` 的模块级函数(``upsert_text`` / ``query_text`` /
``collection_count`` / ``upsert_image`` / ``query_image``)和 ``Doc`` / ``Hit``,
所以 ``from rag.store import query_text`` 这类旧调用无需改动。

注意:R14 之前这里的注释声称"通过 ``RAG_STORE=qdrant`` 支持 Qdrant",
但仓库里从未有过 Qdrant 实现。现在未知的 ``RAG_STORE`` 取值会直接报错,
而不是悄悄退回某个后端。

P1(Milvus 上生产)新增两个可选包装,**默认都不启用**,见 ``rag.store.composite``:

* ``RAG_STORE_SHADOW=milvus|chroma`` —— 影子查询,主存储回答、影子后台陪跑并写 JSONL;
* ``RAG_STORE_FALLBACK=chroma`` + ``RAG_STORE_FALLBACK_UNTIL=YYYY-MM-DD`` —— 主存储抛错时
  限期改由兜底存储回答。

只有"当前配置的存储"(``get_store()`` 不带参数)才会被包装;``get_store("chroma")``
这类显式指定后端的调用(迁移、parity、回放门槛)永远拿到裸存储。
"""

from __future__ import annotations

import os
import threading
from typing import Sequence

from rag.store.base import (
    IMAGE_COLLECTION,
    IMAGE_SCALAR_FIELDS,
    SCHEMA_VERSION,
    TEXT_COLLECTION,
    TEXT_SCALAR_FIELDS,
    Doc,
    Hit,
    VectorStore,
)
from rag.store.chroma_store import CHROMA_DIR, chroma_dir  # CHROMA_DIR:兼容旧引用

SUPPORTED_BACKENDS = ("chroma", "milvus")

_stores: dict[tuple, VectorStore] = {}
_stores_lock = threading.Lock()


def backend_name() -> str:
    return (os.getenv("RAG_STORE") or "chroma").strip().lower()


def get_store(backend: str | None = None) -> VectorStore:
    """返回(按后端 + 连接参数缓存的)存储单例。

    不带参数时返回"当前配置的存储":按 ``RAG_STORE_SHADOW`` / ``RAG_STORE_FALLBACK``
    包上影子 / 兜底(都没设时就是裸存储,与之前完全相同)。
    """
    if backend is None:
        return _configured_store()
    return _raw_store(backend)


def _raw_store(backend: str) -> VectorStore:
    name = backend.strip().lower()
    if name not in SUPPORTED_BACKENDS:
        raise ValueError(f"RAG_STORE={name!r} is not supported; choose one of {SUPPORTED_BACKENDS}")
    key = (name, os.getenv("RAG_MILVUS_URI", "") if name == "milvus" else str(chroma_dir()))
    store = _stores.get(key)
    if store is None:
        with _stores_lock:
            store = _stores.get(key)
            if store is None:
                if name == "milvus":
                    from rag.store.milvus_store import MilvusStore

                    store = MilvusStore()
                else:
                    from rag.store.chroma_store import ChromaStore

                    store = ChromaStore()
                _stores[key] = store
    return store


_wrapped: dict[tuple, VectorStore] = {}


def _wrapper_config() -> tuple[str, str, str, str]:
    primary = backend_name()
    shadow = (os.getenv("RAG_STORE_SHADOW") or "").strip().lower()
    fallback = (os.getenv("RAG_STORE_FALLBACK") or "").strip().lower()
    until = (os.getenv("RAG_STORE_FALLBACK_UNTIL") or "").strip()
    return primary, shadow, fallback, until


def _configured_store() -> VectorStore:
    primary_name, shadow, fallback, until_raw = _wrapper_config()
    primary = _raw_store(primary_name)
    if not shadow and not fallback:
        return primary  # 默认路径:不包装
    key = (primary_name, os.getenv("RAG_MILVUS_URI", ""), shadow, fallback, until_raw)
    store = _wrapped.get(key)
    if store is not None:
        return store
    with _stores_lock:
        store = _wrapped.get(key)
        if store is not None:
            return store
        store = _build_wrapped(primary, primary_name, shadow, fallback, until_raw)
        _wrapped[key] = store
    return store


def _build_wrapped(primary: VectorStore, primary_name: str, shadow: str, fallback: str, until_raw: str) -> VectorStore:
    import sys

    from rag.store.composite import FallbackStore, ShadowStore, parse_until

    store: VectorStore = primary
    # 影子在内、兜底在外:主存储抛错时影子先记下 primary_error,再由兜底接手回答
    if shadow:
        if shadow not in SUPPORTED_BACKENDS:
            print(f"[rag.store] RAG_STORE_SHADOW={shadow!r} is not supported; shadow disabled", file=sys.stderr)
        elif shadow == primary_name:
            print(f"[rag.store] RAG_STORE_SHADOW equals RAG_STORE ({shadow}); shadow disabled", file=sys.stderr)
        else:
            store = ShadowStore(store, lambda: _raw_store(shadow), shadow_name=shadow)
    if fallback:
        until = parse_until(until_raw)
        if fallback not in SUPPORTED_BACKENDS:
            print(f"[rag.store] RAG_STORE_FALLBACK={fallback!r} is not supported; fallback disabled", file=sys.stderr)
        elif fallback == primary_name:
            print(f"[rag.store] RAG_STORE_FALLBACK equals RAG_STORE ({fallback}); fallback disabled", file=sys.stderr)
        elif until is None:
            # 强制限期:没有(或写错)截止日期就不启用,免得两份索引长期不同步
            print(
                f"[rag.store] RAG_STORE_FALLBACK={fallback} needs RAG_STORE_FALLBACK_UNTIL=YYYY-MM-DD "
                f"(got {until_raw!r}); fallback disabled",
                file=sys.stderr,
            )
        else:
            fb = FallbackStore(store, lambda: _raw_store(fallback), fallback_name=fallback, until=until)
            if not fb.active():
                print(
                    f"[rag.store] WARNING: RAG_STORE_FALLBACK_UNTIL={until} has passed; fallback to {fallback} "
                    "is disabled. Remove RAG_STORE_FALLBACK from the config.",
                    file=sys.stderr,
                )
            store = fb
    return store


def write_isolation_required() -> bool:
    """当前后端的写入是否必须放进不加载 torch 的子进程。

    只在 macOS + Milvus Lite + 需要 faiss 的索引类型时为真(OpenMP 双运行时冲突,
    见 ``MilvusStore.write_needs_isolation``)。判断过程不会打开 Lite 数据库文件。
    """
    if backend_name() != "milvus":
        return False
    try:
        return bool(get_store().write_needs_isolation())
    except Exception:
        return False


def describe_store() -> dict:
    """当前后端的索引信息;供 ``/ready`` 展示,失败时返回错误而不抛出。"""
    try:
        return get_store().schema_info()
    except Exception as exc:
        return {"backend": backend_name(), "error": str(exc)}


def serving_collection(store, name: str) -> str:
    """逻辑集合名当前实际落在哪个物理集合(版本化 Milvus 的别名目标);其余情况返回原名。"""
    try:
        target = store.alias_target(name) if hasattr(store, "alias_target") else None
    except Exception:
        target = None
    return target or name


def store_wrapper_stats() -> dict:
    """影子 / 兜底包装的计数(没启用时为空 dict);不触发任何数据库访问。"""
    try:
        from rag.store.composite import wrapper_stats

        return wrapper_stats(get_store())
    except Exception as exc:
        return {"error": str(exc)}


# -- R14 之前的模块级 API(委托给当前后端)-----------------------------------


def upsert_text(docs: Sequence[Doc], embeddings: Sequence[Sequence[float]]) -> None:
    get_store().upsert_text(docs, embeddings)


def query_text(
    embedding: Sequence[float],
    k: int = 5,
    *,
    where: dict | None = None,
) -> list[Hit]:
    return get_store().query_text(embedding, k, where=where)


def collection_count() -> int:
    return get_store().text_count()


def upsert_image(ids: Sequence[str], embeddings: Sequence[Sequence[float]], metadatas: Sequence[dict]) -> None:
    get_store().upsert_image(ids, embeddings, metadatas)


def query_image(embedding: Sequence[float], k: int = 5) -> list[Hit]:
    """Top-k by CLIP image-vector similarity. Returns Hits keyed by product_id."""
    return get_store().query_image(embedding, k)


__all__ = [
    "CHROMA_DIR",
    "Doc",
    "Hit",
    "IMAGE_COLLECTION",
    "IMAGE_SCALAR_FIELDS",
    "SCHEMA_VERSION",
    "SUPPORTED_BACKENDS",
    "serving_collection",
    "TEXT_COLLECTION",
    "TEXT_SCALAR_FIELDS",
    "VectorStore",
    "backend_name",
    "collection_count",
    "describe_store",
    "get_store",
    "query_image",
    "query_text",
    "store_wrapper_stats",
    "upsert_image",
    "upsert_text",
    "write_isolation_required",
]
