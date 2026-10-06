"""Chroma 后端:进程内嵌入式向量库,零依赖,本地开发与当前线上默认使用。

检索行为与 R14 之前的 ``rag/store.py`` 完全一致(相同的集合名、余弦距离、
``score = 1 - distance``),只是收进了 ``VectorStore`` 接口,并补上:

* 客户端单例(不再每次调用都新建 PersistentClient);
* 入库时把 ``SCHEMA_VERSION`` 写进集合 metadata,查询侧据此判断哪些字段能下推;
* ``reset_collection`` 支持 ``--rebuild``。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterator, Sequence

from rag.store.base import (
    IMAGE_COLLECTION,
    IMAGE_SCALAR_FIELDS,
    SCHEMA_VERSION,
    TEXT_COLLECTION,
    TEXT_SCALAR_FIELDS,
    Doc,
    Hit,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHROMA_DIR = REPO_ROOT / "data" / ".chroma"


def chroma_dir() -> Path:
    """Chroma 索引目录。默认 ``data/.chroma``;``RAG_CHROMA_DIR`` 可指向另一份(相对仓库根)。

    P1 用途:VM 上线上那份旧索引一个字节都不动,另建一份 schema v2 的 Chroma
    (例如 ``data/.chroma_v2``)给 migrate / store_parity / replay_gate 当参照。
    """
    raw = (os.getenv("RAG_CHROMA_DIR") or "").strip()
    if not raw:
        return CHROMA_DIR
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p

_SCHEMA_KEY = "lionpick_schema_version"
_PROP_PREFIX = "lionpick_prop_"


class ChromaStore:
    backend = "chroma"

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else chroma_dir()
        self._lock = threading.Lock()
        self._client_obj = None
        self._fields_cache: dict[str, frozenset[str]] = {}

    # -- 内部 -----------------------------------------------------------------

    def _client(self):
        if self._client_obj is None:
            with self._lock:
                if self._client_obj is None:
                    import chromadb

                    self._path.mkdir(parents=True, exist_ok=True)
                    self._client_obj = chromadb.PersistentClient(path=str(self._path))
        return self._client_obj

    def _collection(self, name: str):
        # 新建时带上 schema 版本;已存在的集合 get_or_create 不会改写原有 metadata
        # (已实测 chromadb 0.5.15),所以旧索引保持"无版本号"状态,不会被误判为 v2。
        return self._client().get_or_create_collection(
            name, metadata={"hnsw:space": "cosine", _SCHEMA_KEY: SCHEMA_VERSION}
        )

    @staticmethod
    def _hits(result: dict) -> list[Hit]:
        ids = (result.get("ids") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        return [Hit(id=i, score=1.0 - float(d), metadata=m or {}) for i, m, d in zip(ids, metas, dists)]

    # -- 文本 -----------------------------------------------------------------

    def upsert_text(self, docs: Sequence[Doc], embeddings: Sequence[Sequence[float]]) -> None:
        col = self._collection(TEXT_COLLECTION)
        col.upsert(
            ids=[d.id for d in docs],
            documents=[d.text for d in docs],
            metadatas=[d.metadata for d in docs],
            embeddings=[list(e) for e in embeddings],
        )
        self._fields_cache.pop(TEXT_COLLECTION, None)

    def query_text(
        self,
        embedding: Sequence[float],
        k: int = 5,
        *,
        where: dict | None = None,
    ) -> list[Hit]:
        col = self._collection(TEXT_COLLECTION)
        result = col.query(
            query_embeddings=[list(embedding)],
            n_results=k,
            where=where or None,
        )
        return self._hits(result)

    def text_count(self) -> int:
        return self._collection(TEXT_COLLECTION).count()

    # -- 图片 -----------------------------------------------------------------

    def upsert_image(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict],
    ) -> None:
        col = self._collection(IMAGE_COLLECTION)
        col.upsert(ids=list(ids), embeddings=[list(e) for e in embeddings], metadatas=list(metadatas))
        self._fields_cache.pop(IMAGE_COLLECTION, None)

    def query_image(self, embedding: Sequence[float], k: int = 5) -> list[Hit]:
        col = self._collection(IMAGE_COLLECTION)
        if col.count() == 0:
            return []
        result = col.query(query_embeddings=[list(embedding)], n_results=k)
        return self._hits(result)

    def image_count(self) -> int:
        return self._collection(IMAGE_COLLECTION).count()

    # -- 运维 -----------------------------------------------------------------

    def export(
        self, collection: str, batch_size: int = 1000
    ) -> Iterator[tuple[list[str], list[list[float]], list[dict], list[str | None]]]:
        col = self._collection(collection)
        total = col.count()
        for offset in range(0, total, batch_size):
            r = col.get(
                limit=batch_size,
                offset=offset,
                include=["embeddings", "metadatas", "documents"],
            )
            ids = list(r.get("ids") or [])
            embs = r.get("embeddings")
            vecs = [[float(x) for x in e] for e in (embs if embs is not None else [])]
            metas = [dict(m or {}) for m in (r.get("metadatas") or [])]
            docs = list(r.get("documents") or [None] * len(ids))
            if ids:
                yield ids, vecs, metas, docs

    def reset_collection(self, name: str, properties: dict | None = None) -> None:
        client = self._client()
        try:
            client.delete_collection(name)
        except Exception:
            pass  # 不存在就算了
        self._fields_cache.pop(name, None)
        meta = {"hnsw:space": "cosine", _SCHEMA_KEY: SCHEMA_VERSION}
        for k, v in (properties or {}).items():
            meta[_PROP_PREFIX + str(k)] = str(v)
        # 属性只在建集合时写:对已有集合调用 modify(metadata=...) 会丢掉 hnsw:space(实测)
        client.get_or_create_collection(name, metadata=meta)

    def index_properties(self, collection: str = TEXT_COLLECTION) -> dict:
        meta = self._collection(collection).metadata or {}
        return {k[len(_PROP_PREFIX):]: v for k, v in meta.items() if k.startswith(_PROP_PREFIX)}

    def schema_version(self, collection: str = TEXT_COLLECTION) -> int | None:
        meta = self._collection(collection).metadata or {}
        v = meta.get(_SCHEMA_KEY)
        return int(v) if isinstance(v, (int, float, str)) and str(v).isdigit() else None

    def filterable_fields(self, collection: str = TEXT_COLLECTION) -> frozenset[str]:
        cached = self._fields_cache.get(collection)
        if cached is not None:
            return cached
        declared = TEXT_SCALAR_FIELDS if collection == TEXT_COLLECTION else IMAGE_SCALAR_FIELDS
        version = self.schema_version(collection)
        col = self._collection(collection)
        if version is not None and version >= SCHEMA_VERSION and col.count() > 0:
            fields = frozenset(declared)
        else:
            # 没有版本号的旧索引:以抽样文档实际带的键为准。同一次入库产生的
            # 文档键集合一致(rating 只在评论块上,但它不参与过滤)。
            sample = col.get(limit=1, include=["metadatas"])
            metas = sample.get("metadatas") or []
            keys = set(metas[0].keys()) if metas and metas[0] else set()
            fields = frozenset(k for k in declared if k in keys)
        self._fields_cache[collection] = fields
        return fields

    def schema_info(self) -> dict:
        out: dict = {"backend": self.backend, "path": _display_path(self._path)}
        for label, name in (("text", TEXT_COLLECTION), ("image", IMAGE_COLLECTION)):
            try:
                out[label] = {
                    "count": self._collection(name).count(),
                    "schema_version": self.schema_version(name),
                    "filterable_fields": sorted(self.filterable_fields(name)),
                    "properties": self.index_properties(name),
                }
            except Exception as exc:  # pragma: no cover - 仅用于展示
                out[label] = {"error": str(exc)}
        return out


def _display_path(path: Path) -> str:
    """/ready 是公开接口:只展示相对仓库的路径,不暴露服务器的绝对路径。"""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return Path(path).name
