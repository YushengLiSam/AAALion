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
from rag.store.chroma_store import CHROMA_DIR  # 兼容旧引用

SUPPORTED_BACKENDS = ("chroma", "milvus")

_stores: dict[tuple, VectorStore] = {}
_stores_lock = threading.Lock()


def backend_name() -> str:
    return (os.getenv("RAG_STORE") or "chroma").strip().lower()


def get_store(backend: str | None = None) -> VectorStore:
    """返回(按后端 + 连接参数缓存的)存储单例。"""
    name = (backend or backend_name()).strip().lower()
    if name not in SUPPORTED_BACKENDS:
        raise ValueError(f"RAG_STORE={name!r} is not supported; choose one of {SUPPORTED_BACKENDS}")
    key = (name, os.getenv("RAG_MILVUS_URI", "") if name == "milvus" else str(CHROMA_DIR))
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
    "TEXT_COLLECTION",
    "TEXT_SCALAR_FIELDS",
    "VectorStore",
    "backend_name",
    "collection_count",
    "describe_store",
    "get_store",
    "query_image",
    "query_text",
    "upsert_image",
    "upsert_text",
    "write_isolation_required",
]
