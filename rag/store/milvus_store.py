"""Milvus 后端:显式 schema + HNSW 向量索引 + 标量倒排索引。

同一份代码既能连本地 Milvus Lite(``RAG_MILVUS_URI`` 是文件路径,零运维),也能连
Milvus Standalone / 集群(``RAG_MILVUS_URI=http://host:19530``)。

与 Chroma 的差别主要在 schema:Chroma 的 metadata 是无模式的,写什么存什么;
Milvus 必须事先声明每个字段。这逼着我们把"会被怎么过滤"想清楚——
``brand_country`` / ``currency`` 就是在这里第一次成为一等公民字段的。
不在声明列里的其余 metadata(如评论块的 ``rating``)放进 JSON 列 ``extra``,
读回时再摊平,保证 ``Hit.metadata`` 的形状与 Chroma 后端一致。

pymilvus 只在选用本后端时才 import:线上自动部署脚本不装新依赖,
默认的 Chroma 路径必须在没有 pymilvus 的机器上照常工作。
"""

from __future__ import annotations

import math
import os
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, Sequence

from rag.store.base import (
    IMAGE_COLLECTION,
    IMAGE_SCALAR_FIELDS,
    SCHEMA_VERSION,
    TEXT_COLLECTION,
    TEXT_SCALAR_FIELDS,
    Doc,
    Hit,
)
from rag.store.filters import to_milvus_expr

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URI = str(REPO_ROOT / "data" / ".milvus" / "lionpick.db")

# 标量列定义:(字段名, 类型, VARCHAR 最大长度)。数值列长度填 None。
_VARCHAR = "VARCHAR"
_DOUBLE = "DOUBLE"
_TEXT_COLUMNS: tuple[tuple[str, str, int | None], ...] = (
    ("product_id", _VARCHAR, 128),
    ("chunk_type", _VARCHAR, 32),
    ("category", _VARCHAR, 128),
    ("sub_category", _VARCHAR, 128),
    ("brand", _VARCHAR, 256),
    ("brand_country", _VARCHAR, 8),
    ("currency", _VARCHAR, 8),
    ("base_price", _DOUBLE, None),
)
_IMAGE_COLUMNS: tuple[tuple[str, str, int | None], ...] = tuple(
    c for c in _TEXT_COLUMNS if c[0] in IMAGE_SCALAR_FIELDS
)
# 需要建倒排索引的过滤字段(字符串列)
_INVERTED = ("product_id", "category", "sub_category", "brand", "brand_country", "currency")
_TEXT_MAX = 65535  # Milvus VARCHAR 上限
_PROP_PREFIX = "lionpick."

_HNSW_PARAMS = {"M": 16, "efConstruction": 200}
# 需要 faiss 的索引类型(见 milvus_lite/index/factory.py);FLAT / BRUTE_FORCE 是纯 numpy
_FAISS_INDEX_TYPES = frozenset({"HNSW", "HNSW_SQ", "IVF_FLAT", "IVF_SQ8", "IVF_PQ", "AUTOINDEX"})
_UPSERT_BATCH = int(os.getenv("RAG_MILVUS_UPSERT_BATCH", "2000"))


def _hnsw_ef(k: int) -> int:
    # HNSW 的搜索宽度必须 >= k;给足余量以免小库上召回被 ef 截断
    return max(int(os.getenv("RAG_MILVUS_HNSW_EF", "256")), k)


# ---------------------------------------------------------------------------
# 带过滤搜索的分档策略
#
# HNSW + 标量过滤有个经典坑:过滤越窄,图遍历在 ef 预算内碰到的合格节点越少,
# 结果凑不满 k 个、还会漏掉真正的近邻。R15 一致性比对实测:1082 条里只剩 5% 合格时,
# ef=128 的 recall 只有 0.41(返回 24 条,应有 58 条)。
#
# Milvus 服务端(Knowhere)遇到高选择性过滤会自动改走暴力搜索;Milvus Lite 3.x
# 是 Python 重写版,没有这层逻辑,所以在适配层补上:
#   1. 先用标量倒排索引数出合格条数 m(很便宜);
#   2. m == 0          → 直接返回空;
#   3. m <= 暴力上限    → 把这 m 条的向量取出来精确算余弦,保证不漏;
#   4. 其余            → 走 HNSW,ef 按选择性的倒数放大(有上限)。
# 开关:RAG_MILVUS_FILTER_STRATEGY=adaptive(默认)| plain(只用固定 ef,便于对照实验)
# ---------------------------------------------------------------------------
_BRUTE_MAX = int(os.getenv("RAG_MILVUS_FILTER_BRUTE_MAX", "4096"))
_EF_MAX = int(os.getenv("RAG_MILVUS_HNSW_EF_MAX", "8192"))


def _adaptive_filter_on() -> bool:
    return os.getenv("RAG_MILVUS_FILTER_STRATEGY", "adaptive").strip().lower() != "plain"


class MilvusStore:
    backend = "milvus"

    def __init__(self, uri: str | None = None, token: str | None = None, index_type: str | None = None) -> None:
        self._uri = uri or os.getenv("RAG_MILVUS_URI") or DEFAULT_URI
        self._index_type = (index_type or os.getenv("RAG_MILVUS_INDEX_TYPE") or "HNSW").strip().upper()
        self._token = token if token is not None else os.getenv("RAG_MILVUS_TOKEN", "")
        self._is_lite = "://" not in self._uri
        self._client_obj = None
        self._init_lock = threading.Lock()
        # Milvus Lite 是单进程嵌入式实现,串行化访问更稳妥;远端服务走 gRPC,可并发
        self._op_lock: threading.Lock | None = threading.Lock() if self._is_lite else None
        self._loaded: set[str] = set()
        self._totals: dict[str, int] = {}
        self._pending_props: dict[str, dict] = {}  # reset_collection 时给定,建集合时写入
        self.last_search_path: str = ""  # 调试/压测用:最近一次搜索走了哪一档

    # -- 内部 -----------------------------------------------------------------

    def _client(self):
        if self._client_obj is None:
            with self._init_lock:
                if self._client_obj is None:
                    from pymilvus import MilvusClient

                    if self._is_lite:
                        Path(self._uri).parent.mkdir(parents=True, exist_ok=True)
                        self._warn_embedded_with_torch()
                    try:
                        self._client_obj = MilvusClient(uri=self._uri, token=self._token)
                    except Exception as exc:
                        if self._is_lite:
                            # R15 实测:两个进程同时打开同一个 Lite 数据库文件,后开的
                            # 那个只会得到一句 "Open local milvus failed"。把原因说清楚。
                            raise RuntimeError(
                                f"cannot open Milvus Lite database {self._uri!r}: {exc}. "
                                "Milvus Lite is single-process — another process (a running "
                                "server, an eval, a migration) may hold this file. Use Milvus "
                                "Standalone (RAG_MILVUS_URI=http://host:19530) for multi-process access."
                            ) from exc
                        raise
        return self._client_obj

    def _locked(self, fn, *args, **kwargs):
        if self._op_lock is None:
            return fn(*args, **kwargs)
        with self._op_lock:
            return fn(*args, **kwargs)

    def _call(self, name: str, fn, *args, **kwargs):
        """调用要求集合"已加载"的接口;服务端回"未加载"时,清掉本地缓存、重新加载一次再试。

        R15 审查发现:milvus-lite 服务进程重启后,带索引的集合一律以 released 状态打开,
        而 _loaded 缓存还认为它已加载——此后每次查询都失败,检索悄悄退回关键词兜底,
        直到 API 进程重启。Standalone 上有人 release 了集合也是同样的情况。
        """
        try:
            return self._locked(fn, *args, **kwargs)
        except Exception as exc:
            if not _is_not_loaded(exc):
                raise
            self._loaded.discard(name)
            self._totals.pop(name, None)
            if not self._ensure_loaded(name):
                raise
            return self._locked(fn, *args, **kwargs)

    @staticmethod
    def _columns(name: str) -> tuple[tuple[str, str, int | None], ...]:
        return _TEXT_COLUMNS if name == TEXT_COLLECTION else _IMAGE_COLUMNS

    def _create(self, name: str, dim: int) -> None:
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
            description=f"lionpick schema v{SCHEMA_VERSION}",
        )
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=256)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=int(dim))
        for field, kind, length in self._columns(name):
            if kind == _VARCHAR:
                schema.add_field(field, DataType.VARCHAR, max_length=length)
            else:
                schema.add_field(field, DataType.DOUBLE)
        if name == TEXT_COLLECTION:
            schema.add_field("text", DataType.VARCHAR, max_length=_TEXT_MAX)
        schema.add_field("extra", DataType.JSON)

        index_params = self._client().prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type=self._index_type,
            metric_type="COSINE",
            params=_HNSW_PARAMS if self._index_type.startswith("HNSW") else {},
        )
        for field, _, _ in self._columns(name):
            if field in _INVERTED:
                index_params.add_index(field_name=field, index_type="INVERTED")

        self._client().create_collection(
            name, schema=schema, index_params=index_params, consistency_level="Strong"
        )
        props = self._pending_props.pop(name, None)
        if props:
            self._client().alter_collection_properties(
                name, properties={_PROP_PREFIX + str(k): str(v) for k, v in props.items()}
            )
        self._loaded.add(name)

    def _ensure(self, name: str, dim: int) -> None:
        if not self._locked(self._client().has_collection, name):
            self._locked(self._create, name, dim)

    def _ensure_loaded(self, name: str) -> bool:
        """查询前确保集合已加载;集合不存在返回 False。"""
        if name in self._loaded:
            return True
        client = self._client()
        if not self._locked(client.has_collection, name):
            return False
        self._locked(client.load_collection, name)
        self._loaded.add(name)
        return True

    def _row(self, name: str, id_: str, vector: Sequence[float], meta: dict, text: str | None) -> dict:
        row: dict[str, Any] = {"id": str(id_), "vector": [float(x) for x in vector]}
        declared = set()
        for field, kind, length in self._columns(name):
            declared.add(field)
            val = meta.get(field)
            if kind == _VARCHAR:
                s = "" if val is None else str(val)
                row[field] = _truncate_utf8(s, length) if length else s
            else:
                try:
                    row[field] = float(val) if val is not None else 0.0
                except (TypeError, ValueError):
                    row[field] = 0.0
        if name == TEXT_COLLECTION:
            body = text if text is not None else str(meta.get("text", ""))
            row["text"] = _truncate_utf8(body, _TEXT_MAX)
            declared.add("text")
        row["extra"] = {k: v for k, v in meta.items() if k not in declared and v is not None}
        return row

    def _output_fields(self, name: str) -> list[str]:
        fields = [f for f, _, _ in self._columns(name)]
        if name == TEXT_COLLECTION:
            fields.append("text")
        fields.append("extra")
        return fields

    @staticmethod
    def _to_meta(entity: dict) -> dict:
        meta = {k: v for k, v in entity.items() if k not in ("id", "extra")}
        extra = entity.get("extra") or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                meta.setdefault(k, v)
        return meta

    @property
    def is_lite(self) -> bool:
        return self._is_lite

    def seal(self, name: str) -> None:
        """写完之后落盘(flush)。

        服务模式下向量索引由服务进程自己建,这里只负责把数据持久化。
        R15 曾尝试在这里再做 release → load 以强制嵌入式 Lite 当场建好索引,但实测
        milvus-lite 3.2.1 在写完立刻 release/load 时会读到后台线程尚未写完的段文件
        ("Not an Arrow file" / "File is too small"),FLAT 与 HNSW 都会出现,故放弃。
        落盘失败只告警,不让整次入库失败——数据已经写进去了。
        """
        client = self._client()
        if not self._locked(client.has_collection, name):
            return
        try:
            self._locked(client.flush, name)
        except Exception as exc:  # pragma: no cover - 取决于后端实现
            print(f"[milvus] flush({name}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    _warned_embedded_torch = False

    def _warn_embedded_with_torch(self) -> None:
        """嵌入式 Lite + macOS + faiss 索引 + 本进程带 torch:提示改用服务进程模式。

        R15 实测,这个组合下只要 torch 在 CPU 上做过并行运算,之后任何一次 faiss
        建索引或搜索都会让进程 abort(OMP Error #15);官方提示里的
        KMP_DUPLICATE_LIB_OK=TRUE 也试过,进程会在建索引时静默崩溃,不可用。
        服务模式把 faiss 关进独立进程:``tools/milvus-lite-server.sh`` +
        ``RAG_MILVUS_URI=http://127.0.0.1:19530``。
        """
        if MilvusStore._warned_embedded_torch:
            return
        if "torch" in sys.modules and self.write_needs_isolation():
            MilvusStore._warned_embedded_torch = True
            print(
                "[milvus] embedded Milvus Lite is running inside a process that has loaded torch on macOS. "
                "If torch ever runs a CPU OpenMP op here, the next faiss call aborts the process. "
                "Prefer server mode: tools/milvus-lite-server.sh + RAG_MILVUS_URI=http://127.0.0.1:19530",
                file=sys.stderr,
            )

    def write_needs_isolation(self) -> bool:
        """本进程写 Milvus Lite 会不会触发 macOS 上的 OpenMP 双运行时崩溃。

        R15 实测:torch 和 faiss-cpu 在 macOS 上各自打包了一份 libomp。进程里 torch
        先初始化了一份,milvus-lite 后台用 faiss 建 HNSW 索引时再初始化第二份,
        进程直接 abort("OMP: Error #15"),连异常都抛不出来——服务进程会整个挂掉。
        进一步实测:只要本进程里 torch 的 CPU OpenMP 也被初始化过,faiss 的**搜索**同样
        会 abort,所以这不是"只防写入"就能解决的问题——嵌入式模式只适合不带 torch 的离线
        工具。服务模式(milvus-lite server / Standalone)把 faiss 放在独立进程里,
        不受影响;FLAT 索引是纯 numpy,也不受影响。
        """
        return (
            self._is_lite
            and sys.platform == "darwin"
            and self._index_type in _FAISS_INDEX_TYPES
            and os.getenv("RAG_MILVUS_LITE_ALLOW_TORCH_WRITES", "0") != "1"
        )

    def _upsert(self, name: str, rows: list[dict]) -> None:
        if "torch" in sys.modules and self.write_needs_isolation():
            raise RuntimeError(
                "refusing to write to Milvus Lite from a process that has loaded torch on macOS: "
                "faiss would initialize a second OpenMP runtime while building the HNSW index and "
                "abort the whole process (OMP Error #15). Write from a torch-free process instead "
                "(rag.ingest.* does this automatically via `python -m rag.store.load`), use "
                "RAG_MILVUS_INDEX_TYPE=FLAT, or use Milvus Standalone."
            )
        self._totals.pop(name, None)
        client = self._client()
        for i in range(0, len(rows), _UPSERT_BATCH):
            self._locked(client.upsert, name, data=rows[i : i + _UPSERT_BATCH])

    def _search(self, name: str, embedding: Sequence[float], k: int, expr: str) -> list[Hit]:
        if not self._ensure_loaded(name):
            return []
        ef = _hnsw_ef(k)
        path = "hnsw"
        if expr and _adaptive_filter_on():
            matched = self._count_where(name, expr)
            if matched == 0:
                self.last_search_path = "empty"
                return []
            if matched <= _BRUTE_MAX:
                self.last_search_path = f"exact(m={matched})"
                return self._exact_search(name, embedding, k, expr, matched)
            selectivity = matched / max(1, self._total(name))
            ef = min(_EF_MAX, max(ef, math.ceil(2 * k / selectivity)))
            path = f"hnsw(m={matched},ef={ef})"
        self.last_search_path = path
        res = self._call(
            name,
            self._client().search,
            name,
            data=[[float(x) for x in embedding]],
            limit=int(k),
            filter=expr,
            output_fields=self._output_fields(name),
            search_params={"metric_type": "COSINE", "params": {"ef": ef}},
        )
        hits = res[0] if res else []
        return [
            Hit(id=str(h["id"]), score=float(h["distance"]), metadata=self._to_meta(h.get("entity") or {}))
            for h in hits
        ]

    def _exact_search(self, name: str, embedding: Sequence[float], k: int, expr: str, matched: int) -> list[Hit]:
        """把满足过滤的 m 条向量取出来精确算余弦 top-k(m 已知不超过暴力上限)。"""
        import numpy as np

        rows = self._call(
            name,
            self._client().query,
            name,
            filter=expr,
            output_fields=["vector", *self._output_fields(name)],
            limit=int(matched),
        )
        if not rows:
            return []
        mat = np.asarray([r["vector"] for r in rows], dtype=np.float32)
        mat /= np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
        q = np.asarray(embedding, dtype=np.float32)
        q /= max(float(np.linalg.norm(q)), 1e-12)
        sims = mat @ q
        top = np.argsort(-sims, kind="stable")[: int(k)]
        return [
            Hit(
                id=str(rows[i]["id"]),
                score=float(sims[i]),
                metadata=self._to_meta({kk: vv for kk, vv in rows[i].items() if kk != "vector"}),
            )
            for i in top
        ]

    def _count_where(self, name: str, expr: str) -> int:
        res = self._call(name, self._client().query, name, filter=expr, output_fields=["count(*)"])
        return int(res[0]["count(*)"]) if res else 0

    def _total(self, name: str) -> int:
        n = self._totals.get(name)
        if n is None:
            n = self._count(name)
            self._totals[name] = n
        return n

    def _count(self, name: str) -> int:
        if not self._ensure_loaded(name):
            return 0
        res = self._call(name, self._client().query, name, filter="", output_fields=["count(*)"])
        return int(res[0]["count(*)"]) if res else 0

    # -- 文本 -----------------------------------------------------------------

    def upsert_text(self, docs: Sequence[Doc], embeddings: Sequence[Sequence[float]]) -> None:
        if not docs:
            return
        self._ensure(TEXT_COLLECTION, len(embeddings[0]))
        rows = [
            self._row(TEXT_COLLECTION, d.id, e, d.metadata or {}, d.text)
            for d, e in zip(docs, embeddings)
        ]
        self._upsert(TEXT_COLLECTION, rows)

    def query_text(
        self,
        embedding: Sequence[float],
        k: int = 5,
        *,
        where: dict | None = None,
    ) -> list[Hit]:
        return self._search(TEXT_COLLECTION, embedding, k, to_milvus_expr(where))

    def text_count(self) -> int:
        return self._count(TEXT_COLLECTION)

    # -- 图片 -----------------------------------------------------------------

    def upsert_image(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict],
    ) -> None:
        if not ids:
            return
        self._ensure(IMAGE_COLLECTION, len(embeddings[0]))
        rows = [
            self._row(IMAGE_COLLECTION, i, e, m or {}, None)
            for i, e, m in zip(ids, embeddings, metadatas)
        ]
        self._upsert(IMAGE_COLLECTION, rows)

    def query_image(self, embedding: Sequence[float], k: int = 5) -> list[Hit]:
        return self._search(IMAGE_COLLECTION, embedding, k, "")

    def image_count(self) -> int:
        return self._count(IMAGE_COLLECTION)

    # -- 运维 -----------------------------------------------------------------

    def export(
        self, collection: str, batch_size: int = 1000
    ) -> Iterator[tuple[list[str], list[list[float]], list[dict], list[str | None]]]:
        if not self._ensure_loaded(collection):
            return
        it = self._call(
            collection,
            self._client().query_iterator,
            collection,
            batch_size=batch_size,
            filter="",
            output_fields=["vector", *self._output_fields(collection)],
        )
        try:
            while True:
                rows = self._locked(it.next)
                if not rows:
                    break
                ids, vecs, metas, docs = [], [], [], []
                for row in rows:
                    ids.append(str(row["id"]))
                    vecs.append([float(x) for x in row["vector"]])
                    meta = self._to_meta({k: v for k, v in row.items() if k != "vector"})
                    docs.append(meta.get("text") if collection == TEXT_COLLECTION else None)
                    metas.append(meta)
                yield ids, vecs, metas, docs
        finally:
            self._locked(it.close)

    def reset_collection(self, name: str, properties: dict | None = None) -> None:
        client = self._client()
        if self._locked(client.has_collection, name):
            self._locked(client.drop_collection, name)
        self._loaded.discard(name)
        self._totals.pop(name, None)
        # 维度要等第一批向量到来才知道,所以这里只删不建;upsert 时按需创建,届时写入属性
        if properties:
            self._pending_props[name] = dict(properties)
        else:
            self._pending_props.pop(name, None)

    def index_properties(self, collection: str = TEXT_COLLECTION) -> dict:
        client = self._client()
        if not self._locked(client.has_collection, collection):
            return {}
        props = self._locked(client.describe_collection, collection).get("properties") or {}
        return {k[len(_PROP_PREFIX):]: v for k, v in props.items() if str(k).startswith(_PROP_PREFIX)}

    def filterable_fields(self, collection: str = TEXT_COLLECTION) -> frozenset[str]:
        # 显式 schema:字段在不在,describe 一下就知道,不需要版本号戳
        client = self._client()
        if not self._locked(client.has_collection, collection):
            return frozenset()
        desc = self._locked(client.describe_collection, collection)
        present = {f.get("name") for f in desc.get("fields", [])}
        declared = TEXT_SCALAR_FIELDS if collection == TEXT_COLLECTION else IMAGE_SCALAR_FIELDS
        return frozenset(f for f in declared if f in present)

    def schema_info(self) -> dict:
        out: dict = {
            "backend": self.backend,
            "uri": _display_uri(self._uri) if self._is_lite else self._uri.split("@")[-1],
            "mode": "lite" if self._is_lite else "server",
            "index_type": self._index_type,
        }
        for label, name in (("text", TEXT_COLLECTION), ("image", IMAGE_COLLECTION)):
            try:
                fields = self.filterable_fields(name)
                out[label] = {
                    "count": self._count(name),
                    "schema_version": SCHEMA_VERSION if fields else None,
                    "filterable_fields": sorted(fields),
                    "properties": self.index_properties(name),
                }
            except Exception as exc:  # pragma: no cover - 仅用于展示
                out[label] = {"error": str(exc)}
        return out


def _is_not_loaded(exc: Exception) -> bool:
    """服务端"集合未加载"错误(milvus-lite 与 Milvus 都用 code 101)。"""
    msg = str(exc).lower()
    return getattr(exc, "code", None) == 101 or "call load()" in msg or "not loaded" in msg or "'released'" in msg


def _display_uri(uri: str) -> str:
    """/ready 是公开接口:Lite 文件只展示相对仓库的路径。"""
    try:
        return str(Path(uri).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return Path(uri).name


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """Milvus VARCHAR 的 max_length 按字节计;中文 3 字节,按字节安全截断。"""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore")


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Milvus store maintenance")
    ap.add_argument("--seal", metavar="URI", required=True, help="flush + build/persist indexes for all LionPick collections")
    ap.add_argument("--index-type", default=None)
    args = ap.parse_args(argv)
    store = MilvusStore(uri=args.seal, index_type=args.index_type)
    for name in (TEXT_COLLECTION, IMAGE_COLLECTION):
        store.seal(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
