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

P1 —— 版本化集合 + 别名(``RAG_MILVUS_VERSIONED=1``,默认 0 = 行为不变):
业务只认逻辑名 ``products_text`` / ``products_image``;开启后每次 ``--rebuild``
(入库 / ``rag.store.load`` / ``rag.store.migrate``)都写进一个新的物理集合
``products_text__v2_<YYYYmmddHHMM>``,写完 ``seal()`` 时核对条数,再把别名
``products_text`` 指过去(create / alter alias)。旧的物理集合保留做回滚
(默认保留 2 个,永远不删别名正指向的那个),见 ``python -m rag.store.alias``。
读路径始终用逻辑名,别名切换对正在服务的进程是透明的。
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
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

# 物理集合命名:<逻辑名>__v<schema 版本>_<YYYYmmddHHMM>[_<序号>];
# 首次启用别名时,原来那个"直接叫逻辑名"的老集合会被改名为 <逻辑名>__legacy_<时间戳>。
_PHYSICAL_RE = re.compile(
    r"^(?P<logical>[A-Za-z_][A-Za-z0-9_]*?)__(?:v(?P<ver>\d+)|legacy)_(?P<ts>\d{12})(?:_(?P<seq>\d+))?$"
)
LOGICAL_COLLECTIONS = (TEXT_COLLECTION, IMAGE_COLLECTION)


def logical_name(name: str) -> str:
    """物理集合名 → 逻辑名;本身就是逻辑名(或不认识的名字)时原样返回。"""
    m = _PHYSICAL_RE.match(name or "")
    return m.group("logical") if m else name


def is_physical_name(name: str) -> bool:
    return bool(_PHYSICAL_RE.match(name or ""))


def physical_sort_key(name: str) -> tuple[str, int]:
    """按建表时间(再按同一分钟内的序号)排序;不认识的名字排最前。

    legacy 集合是在第一次切别名时才改名的,时间戳可能和同一分钟里新建的 v2 集合相同,
    但它的数据一定更老,所以同一分钟内排在最前(序号记 0)。
    """
    m = _PHYSICAL_RE.match(name or "")
    if not m:
        return ("", 0)
    seq = 0 if m.group("ver") is None else int(m.group("seq") or 1)
    return (m.group("ts"), seq)


def new_physical_name(
    logical: str,
    existing: set[str] | frozenset[str],
    now: _dt.datetime | None = None,
    kind: str | None = None,
) -> str:
    """生成一个不与现有集合 / 别名重名的物理集合名(同一分钟内重建时加序号)。"""
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d%H%M")
    base = f"{logical}__{kind or f'v{SCHEMA_VERSION}'}_{stamp}"
    name, seq = base, 1
    while name in existing:
        seq += 1
        name = f"{base}_{seq}"
    return name


# ---------------------------------------------------------------------------
# 读 RPC 超时(P1 审查补)
#
# pymilvus 3.0.2 对 UNAVAILABLE 默认重试 75 次、退避封顶 3 s,且不带 deadline。实测
# (milvus-lite 服务进程被 SIGSTOP,模拟"端口还在、服务不应答"):一次 search 要 68 s
# 才抛错。这期间 FallbackStore / 关键词兜底都接不住,聊天请求等于挂死。连接被拒
# (容器停了)时 3.0.2 会立刻失败,不受影响。
#
# 所以服务模式下读路径的 RPC 默认带 ``RAG_MILVUS_TIMEOUT_S``(默认 5 s;1082 条时一次
# 搜索是毫秒级,精确计算那一档 4096 条也在 0.5 s 内)。load_collection 单独用
# ``RAG_MILVUS_LOAD_TIMEOUT_S``(默认 60 s)。写入(upsert / flush / 建删集合 / 别名)
# 不加超时,保持原样。Lite 文件模式默认不加(本地压测跑大库时搜索可能超过 5 s),
# 显式设置了环境变量才加。设为 0 = 不加超时(R15 的原始行为)。
# ---------------------------------------------------------------------------
_READ_METHODS = frozenset({
    "search", "query", "get", "has_collection", "describe_collection", "list_aliases",
    "describe_alias", "list_collections", "get_collection_stats",
})


def _rpc_timeouts(is_lite: bool) -> tuple[float | None, float | None]:
    """(读超时, load 超时);None = 不加。"""

    def one(name: str, default: float) -> float | None:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return None if is_lite else default
        try:
            v = float(raw)
        except ValueError:
            return None if is_lite else default
        return v if v > 0 else None

    return one("RAG_MILVUS_TIMEOUT_S", 5.0), one("RAG_MILVUS_LOAD_TIMEOUT_S", 60.0)


class _TimeoutClient:
    """给 MilvusClient 的读方法补上 ``timeout=``(调用方显式传了就不覆盖);其余原样转发。"""

    def __init__(self, client, read_timeout: float | None, load_timeout: float | None) -> None:
        self._raw = client
        self._read_timeout = read_timeout
        self._load_timeout = load_timeout

    def __getattr__(self, name: str):
        attr = getattr(self._raw, name)
        if name in _READ_METHODS:
            t = self._read_timeout
        elif name == "load_collection":
            t = self._load_timeout
        else:
            return attr
        if t is None or not callable(attr):
            return attr

        def call(*args, **kwargs):
            kwargs.setdefault("timeout", t)
            return attr(*args, **kwargs)

        return call


_HNSW_PARAMS = {"M": 16, "efConstruction": 200}
# 需要 faiss 的索引类型(见 milvus_lite/index/factory.py);FLAT / BRUTE_FORCE 是纯 numpy
_FAISS_INDEX_TYPES = frozenset({"HNSW", "HNSW_SQ", "IVF_FLAT", "IVF_SQ8", "IVF_PQ", "AUTOINDEX"})
_UPSERT_BATCH = int(os.getenv("RAG_MILVUS_UPSERT_BATCH", "2000"))


def _hnsw_ef(k: int) -> int:
    # HNSW 的搜索宽度必须 >= k;给足余量以免小库上召回被 ef 截断
    return max(int(os.getenv("RAG_MILVUS_HNSW_EF", "1024")), k)


# ---------------------------------------------------------------------------
# 带过滤搜索的分档策略
#
# HNSW + 标量过滤有个经典坑:过滤越窄,图遍历在 ef 预算内碰到的合格节点越少,
# 结果凑不满 k 个、还会漏掉真正的近邻。Milvus 服务端(Knowhere)遇到高选择性过滤会
# 自动改走暴力搜索;Milvus Lite 3.x 是 Python 重写版,没有这层逻辑,所以在适配层补上:
#   1. 先用标量倒排索引数出合格条数 m(很便宜);
#   2. m == 0            → 直接返回空;
#   3. m <= 暴力上限      → 只拉这 m 条的向量精确算余弦,再只取 top-k 的元数据;
#   4. 其余              → 走 HNSW,ef = 系数 × k / 选择性(有上限)。
#
# 参数是量出来的(docs/bench/calibrate_{10k,100k}.json,KuaiSearch 真实商品,k=60):
#   - 固定 ef 时,越窄的类目过滤召回越低:10 万条、ef=1024,选择性 17% / 3% / 0.1%
#     的 recall@10 只有 0.69 / 0.57 / 0.31。类目和查询语义相关(同类商品在向量空间里
#     聚成一团),查询不在那一团附近时,图遍历在预算内很难走进去。
#   - 17% 的类目过滤要 ef≈16384 才到 0.99 → 系数 ≈ 48(1 万条时量出的 12 到 10 万不够;
#     最初拍的 2 差了一个数量级)。品牌排除这类过滤 ef=1024 就够,但查询时无法廉价判断
#     过滤和语义相不相关,按最坏情况取值:用排除类多花的延迟换类目过滤不丢召回。
#   - 精确计算的成本 ≈ 100 ms + 每条合格向量 0.12 ms(服务模式每次 RPC 约 50 ms 固定开销),
#     合格 3000 条时 449 ms 满召回,优于 ef=16384 的 669 ms / 0.962,故暴力上限取 4096。
#   - 不带过滤时 ef 从 256 提到 1024,召回 0.991 → 1.000,只多约 7 ms。
# 开关:RAG_MILVUS_FILTER_STRATEGY=adaptive(默认)| plain(只用固定 ef,便于对照实验)
# ---------------------------------------------------------------------------
_BRUTE_MAX = int(os.getenv("RAG_MILVUS_FILTER_BRUTE_MAX", "4096"))
_EF_MAX = int(os.getenv("RAG_MILVUS_HNSW_EF_MAX", "16384"))
_EF_FACTOR = float(os.getenv("RAG_MILVUS_FILTER_EF_FACTOR", "48"))
# 合格条数缓存:count 在 milvus-lite 服务模式下有固定的 RPC 开销,`not in` 之类还可能全表扫;
# 同一个过滤条件(比如"女装")在生产里会反复出现。缓存只影响走哪一档,不影响正确性——
# 精确计算那一档会用 上限+1 做 limit,发现真实条数已超上限就退回 HNSW。
_COUNT_TTL = float(os.getenv("RAG_MILVUS_COUNT_CACHE_TTL", "300"))
_COUNT_CACHE_MAX = 512


def _adaptive_filter_on() -> bool:
    return os.getenv("RAG_MILVUS_FILTER_STRATEGY", "adaptive").strip().lower() != "plain"


class MilvusStore:
    backend = "milvus"

    def __init__(
        self,
        uri: str | None = None,
        token: str | None = None,
        index_type: str | None = None,
        *,
        versioned: bool | None = None,
        db_name: str | None = None,
    ) -> None:
        self._uri = uri or os.getenv("RAG_MILVUS_URI") or DEFAULT_URI
        self._index_type = (index_type or os.getenv("RAG_MILVUS_INDEX_TYPE") or "HNSW").strip().upper()
        self._token = token if token is not None else os.getenv("RAG_MILVUS_TOKEN", "")
        # 库名与一致性级别可配(PLAN P1)。1082 条、没有流式写入时一致性级别的差别可以忽略,
        # 不当卖点;默认值保持 R15 的行为(默认库 + Strong)。
        self._db_name = (db_name if db_name is not None else os.getenv("RAG_MILVUS_DB", "")).strip()
        self._consistency = (os.getenv("RAG_MILVUS_CONSISTENCY") or "Strong").strip()
        self._versioned = (
            versioned if versioned is not None else os.getenv("RAG_MILVUS_VERSIONED", "0").strip() == "1"
        )
        try:
            self._retain = max(1, int(os.getenv("RAG_MILVUS_RETAIN", "2")))
        except ValueError:
            self._retain = 2
        # 版本化模式下 reset_collection 只登记"这次重建写进哪个新物理集合",真正切别名在 seal()
        self._staging: dict[str, dict] = {}
        self._is_lite = "://" not in self._uri
        self._client_obj = None
        self._init_lock = threading.Lock()
        # Milvus Lite 是单进程嵌入式实现,串行化访问更稳妥;远端服务走 gRPC,可并发
        # 可重入:_create 在持锁状态下还要查别名(alias_target 自己也会加锁)
        self._op_lock: threading.RLock | None = threading.RLock() if self._is_lite else None
        self._loaded: set[str] = set()
        self._totals: dict[str, int] = {}
        self._pending_props: dict[str, dict] = {}  # reset_collection 时给定,建集合时写入
        self._count_cache: dict[tuple[str, str], tuple[float, int]] = {}
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
                        kwargs = {"db_name": self._db_name} if self._db_name else {}
                        read_t, load_t = _rpc_timeouts(self._is_lite)
                        if read_t is not None:
                            kwargs["timeout"] = read_t  # 建连超时;服务不应答时首次连接也不会无限等
                        client = MilvusClient(uri=self._uri, token=self._token, **kwargs)
                        if read_t is not None or load_t is not None:
                            client = _TimeoutClient(client, read_t, load_t)
                        self._client_obj = client
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
        return _TEXT_COLUMNS if logical_name(name) == TEXT_COLLECTION else _IMAGE_COLUMNS

    @staticmethod
    def _is_text(name: str) -> bool:
        return logical_name(name) == TEXT_COLLECTION

    def _create(self, name: str, dim: int) -> None:
        from pymilvus import DataType, MilvusClient

        # 绝不建一个与现有别名同名的物理集合:Milvus 会直接报错,Lite 会和别名解析搅在一起
        if self.alias_target(name) is not None:
            raise RuntimeError(
                f"refusing to create collection {name!r}: an alias with that name already exists "
                "(this index is versioned; rebuild with RAG_MILVUS_VERSIONED=1 or manage it with "
                "`python -m rag.store.alias`)"
            )

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
        if self._is_text(name):
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
            name, schema=schema, index_params=index_params, consistency_level=self._consistency
        )
        props = self._pending_props.pop(name, None)
        if props:
            self._client().alter_collection_properties(
                name, properties={_PROP_PREFIX + str(k): str(v) for k, v in props.items()}
            )
        self._loaded.add(name)

    def _ensure(self, name: str, dim: int) -> None:
        if self._locked(self._client().has_collection, name):
            return
        if self.alias_target(name) is not None:
            return  # 别名存在:写入经由别名落到它指向的物理集合
        self._locked(self._create, name, dim)

    def _ensure_loaded(self, name: str) -> bool:
        """查询前确保集合已加载;集合不存在返回 False。"""
        if name in self._loaded:
            return True
        client = self._client()
        if not self._exists(name):
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
        if self._is_text(name):
            body = text if text is not None else str(meta.get("text", ""))
            row["text"] = _truncate_utf8(body, _TEXT_MAX)
            declared.add("text")
        row["extra"] = {k: v for k, v in meta.items() if k not in declared and v is not None}
        return row

    def _output_fields(self, name: str) -> list[str]:
        fields = [f for f, _, _ in self._columns(name)]
        if self._is_text(name):
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

        版本化模式下,如果这个集合在本进程里刚被 ``reset_collection`` 过,seal 就是
        "提交点":核对新物理集合的条数,无误后才把别名切过去,再按保留策略清理旧集合。
        条数不符时**不切别名**并抛错,线上读到的仍是旧集合。
        """
        staged = self._staging.get(name)
        if staged is not None:
            self._commit_staged(name, staged)
            return
        client = self._client()
        if not self._exists(name):
            return
        target = self.alias_target(name) or name
        try:
            self._locked(client.flush, target)
        except Exception as exc:  # pragma: no cover - 取决于后端实现
            print(f"[milvus] flush({target}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)

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
        self._drop_counts(name)
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
                hits = self._exact_search(name, embedding, k, expr)
                if hits is not None:
                    self.last_search_path = f"exact(m={matched})"
                    return hits
                # 缓存的条数过期了,真实条数已超上限:重新数一次,走 HNSW
                self._count_cache.pop((name, expr), None)
                matched = self._count_where(name, expr)
            selectivity = matched / max(1, self._total(name))
            ef = min(_EF_MAX, max(ef, math.ceil(_EF_FACTOR * k / selectivity)))
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

    def _exact_search(self, name: str, embedding: Sequence[float], k: int, expr: str) -> list[Hit] | None:
        """满足过滤的向量精确算余弦 top-k;合格条数超过暴力上限时返回 None(由调用方改走 HNSW)。

        分两步拉取:先只要 id + 向量(实测比连同全部字段一起拉快约一倍),
        算出 top-k 后再只取这 k 条的元数据。
        """
        import numpy as np

        rows = self._call(
            name,
            self._client().query,
            name,
            filter=expr,
            output_fields=["vector"],
            limit=_BRUTE_MAX + 1,
        )
        if len(rows) > _BRUTE_MAX:
            return None
        if not rows:
            return []
        mat = np.asarray([r["vector"] for r in rows], dtype=np.float32)
        mat /= np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
        q = np.asarray(embedding, dtype=np.float32)
        q /= max(float(np.linalg.norm(q)), 1e-12)
        sims = mat @ q
        top = np.argsort(-sims, kind="stable")[: int(k)]
        top_ids = [str(rows[i]["id"]) for i in top]
        meta_rows = self._call(name, self._client().get, name, ids=top_ids, output_fields=self._output_fields(name))
        by_id = {str(r["id"]): r for r in (meta_rows or [])}
        return [
            Hit(id=top_ids[j], score=float(sims[i]), metadata=self._to_meta(by_id.get(top_ids[j], {})))
            for j, i in enumerate(top)
        ]

    def _count_where(self, name: str, expr: str) -> int:
        import time as _time

        key = (name, expr)
        now = _time.monotonic()
        hit = self._count_cache.get(key)
        # 缓存的 0 不用:_search 见到 0 会直接返回空结果,而别名可能已被别的进程(入库 /
        # rag.store.alias)切到一份数据不同的物理集合——缓存键是逻辑名,感知不到切换。
        # 非 0 的缓存只影响走哪一档(精确那档会用 上限+1 复核),不影响正确性。
        if hit is not None and hit[1] > 0 and now - hit[0] <= _COUNT_TTL:
            return hit[1]
        res = self._call(name, self._client().query, name, filter=expr, output_fields=["count(*)"])
        n = int(res[0]["count(*)"]) if res else 0
        if len(self._count_cache) >= _COUNT_CACHE_MAX:
            self._count_cache.clear()
        self._count_cache[key] = (now, n)
        return n

    def _drop_counts(self, name: str) -> None:
        for key in [k for k in self._count_cache if k[0] == name]:
            self._count_cache.pop(key, None)

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
        target = self._write_target(TEXT_COLLECTION)
        self._ensure(target, len(embeddings[0]))
        rows = [
            self._row(target, d.id, e, d.metadata or {}, d.text)
            for d, e in zip(docs, embeddings)
        ]
        self._upsert(target, rows)
        self._note_staged(TEXT_COLLECTION, rows)

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
        target = self._write_target(IMAGE_COLLECTION)
        self._ensure(target, len(embeddings[0]))
        rows = [
            self._row(target, i, e, m or {}, None)
            for i, e, m in zip(ids, embeddings, metadatas)
        ]
        self._upsert(target, rows)
        self._note_staged(IMAGE_COLLECTION, rows)

    def query_image(self, embedding: Sequence[float], k: int = 5) -> list[Hit]:
        return self._search(IMAGE_COLLECTION, embedding, k, "")

    def image_count(self) -> int:
        return self._count(IMAGE_COLLECTION)

    # -- 版本化集合与别名(P1)--------------------------------------------------

    def alias_target(self, alias: str) -> str | None:
        """别名指向的物理集合;不是别名(或后端不支持别名接口)时返回 None。

        先用 list_aliases 判断存不存在:对不存在的别名直接 describe_alias,pymilvus 会把
        "alias does not exist" 当 RPC 错误连同堆栈打进日志(实测),/ready 每次探测都会刷屏。
        """
        try:
            listed = self._locked(self._client().list_aliases)
            names = listed.get("aliases") if isinstance(listed, dict) else listed
            if names is not None and alias not in set(names):
                return None
        except Exception:
            pass  # 列不出来就直接 describe,让下面的 try 兜住
        try:
            desc = self._locked(self._client().describe_alias, alias)
        except Exception:
            return None
        if not isinstance(desc, dict):
            return None
        # pymilvus 返回 collection_name;Milvus Lite 内部实现用 collection,两种都认
        target = desc.get("collection_name") or desc.get("collection")
        return str(target) if target else None

    def _exists(self, name: str) -> bool:
        """集合或别名存在。Milvus Lite 与 Standalone 的 has_collection 都会解析别名(lite 读码确认,
        Standalone 为文档 / 读码推断,未实测);这里再查一次别名兜底,免得读路径悄悄返回空。"""
        if self._locked(self._client().has_collection, name):
            return True
        return self.alias_target(name) is not None

    def physical_collections(self, logical: str) -> list[str]:
        """某个逻辑集合名下的全部物理集合(版本化的 + 改名留下的 legacy),按建表时间升序。"""
        names = [
            str(n) for n in (self._locked(self._client().list_collections) or [])
            if is_physical_name(str(n)) and logical_name(str(n)) == logical
        ]
        return sorted(names, key=physical_sort_key)

    def _write_target(self, logical: str) -> str:
        staged = self._staging.get(logical)
        return staged["physical"] if staged else logical

    def _note_staged(self, logical: str, rows: list[dict]) -> None:
        staged = self._staging.get(logical)
        if staged is not None:
            staged["ids"].update(r["id"] for r in rows)

    def _physical_count(self, physical: str) -> int:
        client = self._client()
        self._locked(client.load_collection, physical)
        res = self._locked(client.query, physical, filter="", output_fields=["count(*)"])
        return int(res[0]["count(*)"]) if res else 0

    def _commit_staged(self, logical: str, staged: dict) -> None:
        client = self._client()
        physical = staged["physical"]
        expected = len(staged["ids"])
        if not self._locked(client.has_collection, physical):
            self._staging.pop(logical, None)
            print(f"[milvus] nothing was written to {physical!r}; alias {logical!r} unchanged", file=sys.stderr)
            return
        try:
            self._locked(client.flush, physical)
        except Exception as exc:  # pragma: no cover - 取决于后端实现
            print(f"[milvus] flush({physical}) failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        n = self._physical_count(physical)
        if n <= 0 or n != expected:
            # 不切别名:线上继续读旧集合。这个半成品从没服务过,直接删掉——留着的话它比
            # 真正的上一版"更新",下次重建时会按保留策略把可回滚的那一版挤掉。
            self._staging.pop(logical, None)
            try:
                self._locked(client.drop_collection, physical)
            except Exception as exc:  # pragma: no cover - 尽力而为
                print(f"[milvus] could not drop failed staging collection {physical!r}: {exc}", file=sys.stderr)
            raise RuntimeError(
                f"staged collection {physical!r} has {n} rows, expected {expected}; "
                f"alias {logical!r} NOT switched"
            )
        previous = self.switch_alias(logical, physical)
        self._staging.pop(logical, None)
        # 保留的那一版必须是"刚才在服务的":如果之前回滚过(别名指向较老的一版),
        # 只按时间留最新的会把可回滚的好版本删掉、留下当初被回滚掉的坏版本
        dropped = self.prune(logical, protect=previous)
        print(
            f"[milvus] alias {logical!r} -> {physical!r} ({n} rows); previous={previous!r}; "
            f"pruned={dropped}",
            file=sys.stderr,
        )

    def _invalidate(self, logical: str) -> None:
        self._loaded.discard(logical)
        self._totals.pop(logical, None)
        self._drop_counts(logical)

    def switch_alias(self, logical: str, physical: str) -> str | None:
        """把别名 ``logical`` 指向 ``physical``(没有别名就创建)。返回之前指向的物理集合。

        首次启用别名时,如果库里已经有一个直接叫逻辑名的老集合(R15 的非版本化索引),
        先把它改名为 ``<逻辑名>__legacy_<时间戳>``——保留下来做回滚,而不是删掉;
        改名到建别名之间有毫秒级的窗口逻辑名解析不到,读路径会走关键词兜底(已计数)。
        """
        if logical_name(physical) != logical or physical == logical:
            raise ValueError(f"{physical!r} is not a physical collection of {logical!r}")
        client = self._client()
        if not self._locked(client.has_collection, physical):
            raise RuntimeError(f"collection {physical!r} does not exist")
        current = self.alias_target(logical)
        previous = current
        if current is None:
            if self._locked(client.has_collection, logical):
                existing = set(self._locked(client.list_collections) or [])
                legacy = new_physical_name(logical, existing, kind="legacy")
                self._locked(client.rename_collection, logical, legacy)
                previous = legacy
                print(f"[milvus] renamed legacy collection {logical!r} -> {legacy!r}", file=sys.stderr)
            self._locked(client.create_alias, physical, logical)
        elif current != physical:
            self._locked(client.alter_alias, physical, logical)
        self._invalidate(logical)
        return previous

    def prune(self, logical: str, retain: int | None = None, protect: str | None = None) -> list[str]:
        """保留 ``retain`` 个物理集合,其余删除。优先级:别名指向的那个(永远不删)>
        ``protect``(切别名前在服务的那一版,回滚目标)> 按建表时间从新到旧。

        保留下来但不在服务的旧集合会 release 掉,省内存(回滚时 ``rag.store.alias`` 会先 load)。
        """
        keep_n = self._retain if retain is None else max(1, int(retain))
        client = self._client()
        target = self.alias_target(logical)
        names = self.physical_collections(logical)
        keep: set[str] = set()
        for name in [target, protect, *reversed(names)]:
            if len(keep) >= keep_n:
                break
            if name and name in names:
                keep.add(name)
        if target:
            keep.add(target)
        dropped = []
        for name in names:
            if name in keep or name == logical or name == target:
                continue
            self._locked(client.drop_collection, name)
            self._loaded.discard(name)
            dropped.append(name)
        for name in names:
            if name in keep and name != target:
                try:
                    self._locked(client.release_collection, name)
                except Exception:
                    pass
        return dropped

    def rollback_alias(self, logical: str) -> tuple[str, str]:
        """把别名退回到比当前目标更早的、最新的那个物理集合。返回 (原目标, 新目标)。"""
        target = self.alias_target(logical)
        if target is None:
            raise RuntimeError(f"{logical!r} is not an alias; nothing to roll back")
        older = [n for n in self.physical_collections(logical) if physical_sort_key(n) < physical_sort_key(target)]
        if not older:
            raise RuntimeError(f"no physical collection older than {target!r} is retained for {logical!r}")
        new = older[-1]
        self.activate(new)
        return target, new

    def activate(self, physical: str) -> str | None:
        """把 ``physical`` 加载、校验(非空、字段齐全)后设为其逻辑名别名的目标。"""
        logical = logical_name(physical)
        if logical == physical or logical not in LOGICAL_COLLECTIONS:
            raise ValueError(f"{physical!r} is not a LionPick physical collection name")
        n = self._physical_count(physical)
        if n <= 0:
            raise RuntimeError(f"refusing to switch {logical!r} to empty collection {physical!r}")
        desc = self._locked(self._client().describe_collection, physical)
        present = {f.get("name") for f in desc.get("fields", [])}
        declared = TEXT_SCALAR_FIELDS if logical == TEXT_COLLECTION else IMAGE_SCALAR_FIELDS
        missing = [f for f in declared if f not in present]
        if missing:
            raise RuntimeError(f"{physical!r} lacks schema v{SCHEMA_VERSION} fields {missing}")
        return self.switch_alias(logical, physical)

    def alias_status(self) -> dict:
        """供 ``rag.store.alias --list`` 展示:每个逻辑名的别名目标与保留的物理集合。"""
        client = self._client()
        out: dict = {}
        for logical in LOGICAL_COLLECTIONS:
            target = self.alias_target(logical)
            rows = []
            for name in self.physical_collections(logical):
                try:
                    stats = self._locked(client.get_collection_stats, name) or {}
                    count = int(stats.get("row_count", 0))
                except Exception:
                    count = None
                rows.append({"name": name, "rows": count, "serving": name == target})
            plain = target is None and bool(self._locked(client.has_collection, logical))
            out[logical] = {"alias_target": target, "unversioned_collection": plain, "physical": rows}
        return out

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
                    docs.append(meta.get("text") if self._is_text(collection) else None)
                    metas.append(meta)
                yield ids, vecs, metas, docs
        finally:
            self._locked(it.close)

    def reset_collection(self, name: str, properties: dict | None = None) -> None:
        client = self._client()
        if self._versioned and name in LOGICAL_COLLECTIONS:
            # 版本化:不删任何东西,只登记这次重建要写入的新物理集合;别名在 seal() 时才切
            existing = set(self._locked(client.list_collections) or [])
            existing |= {st["physical"] for st in self._staging.values()}
            existing |= set(LOGICAL_COLLECTIONS)
            physical = new_physical_name(name, existing)
            self._staging[name] = {"physical": physical, "ids": set()}
            self._loaded.discard(physical)
            self._totals.pop(physical, None)
            if properties:
                self._pending_props[physical] = dict(properties)
            else:
                self._pending_props.pop(physical, None)
            print(f"[milvus] versioned rebuild of {name!r} -> staging collection {physical!r}", file=sys.stderr)
            return
        if self.alias_target(name) is not None:
            # 非版本化模式下绝不能经由别名删集合:Milvus 会拒绝,而 Milvus Lite 会把别名
            # 指向的物理集合(也就是线上正在读的那份)直接删掉(milvus_lite/db.py 先解析别名)。
            raise RuntimeError(
                f"{name!r} is an alias (versioned index). Rebuild with RAG_MILVUS_VERSIONED=1, "
                "or roll back / switch with `python -m rag.store.alias`."
            )
        if self._locked(client.has_collection, name):
            self._locked(client.drop_collection, name)
        self._loaded.discard(name)
        self._totals.pop(name, None)
        self._drop_counts(name)
        # 维度要等第一批向量到来才知道,所以这里只删不建;upsert 时按需创建,届时写入属性
        if properties:
            self._pending_props[name] = dict(properties)
        else:
            self._pending_props.pop(name, None)

    def index_properties(self, collection: str = TEXT_COLLECTION) -> dict:
        client = self._client()
        if not self._exists(collection):
            return {}
        props = self._locked(client.describe_collection, collection).get("properties") or {}
        return {k[len(_PROP_PREFIX):]: v for k, v in props.items() if str(k).startswith(_PROP_PREFIX)}

    def filterable_fields(self, collection: str = TEXT_COLLECTION) -> frozenset[str]:
        # 显式 schema:字段在不在,describe 一下就知道,不需要版本号戳
        client = self._client()
        if not self._exists(collection):
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
            "versioned": self._versioned,
        }
        if self._db_name:
            out["db"] = self._db_name
        for label, name in (("text", TEXT_COLLECTION), ("image", IMAGE_COLLECTION)):
            try:
                fields = self.filterable_fields(name)
                out[label] = {
                    "count": self._count(name),
                    "schema_version": SCHEMA_VERSION if fields else None,
                    "filterable_fields": sorted(fields),
                    "properties": self.index_properties(name),
                    # 别名指向的具体物理集合;没有别名(非版本化)时就是逻辑名本身
                    "physical": self.alias_target(name) or name,
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
