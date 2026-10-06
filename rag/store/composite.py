"""组合存储:影子查询(ShadowStore)与限期兜底(FallbackStore)。

两者都是对 ``VectorStore`` 的透明包装,业务代码感知不到;由 ``rag.store.get_store()``
按环境变量装配,**默认都不启用**(不设变量 = 行为与之前完全相同)。

ShadowStore —— 迁移的"影子"阶段(PLAN P1 第 4 步)::

    RAG_STORE=chroma RAG_STORE_SHADOW=milvus      # 正向影子:Chroma 答,Milvus 陪跑
    RAG_STORE=milvus RAG_STORE_SHADOW=chroma      # 反向影子:切换后 Chroma 陪跑

主存储照常回答;同一个查询再丢进一个**有界**后台线程池发给影子存储,
结果比对后追加一行 JSONL 到 ``RAG_SHADOW_LOG``(默认 ``data/.shadow/shadow.jsonl``)。
硬约束:影子永远不能抛进主路径、也不能拖慢主路径——

* 线程数 1–2(``RAG_SHADOW_WORKERS``,daemon 线程,进程退出时不等影子任务),
  在途上限 ``RAG_SHADOW_QUEUE``(默认 64),满了直接丢弃并计数,不阻塞;
* 单次影子查询超过 ``RAG_SHADOW_TIMEOUT_MS``(默认 2000)记为 timeout。Python 线程
  杀不掉,所以超时的查询仍会在后台跑完——但它只占影子自己的线程,队列满了后续影子
  请求就被丢弃,主路径不受影响;
* 主路径上只做"提交任务"这一件事(微秒级),比对、算 hash、写文件全在后台线程。

FallbackStore —— 运行时降级(PLAN 回滚 L3)::

    RAG_STORE=milvus RAG_STORE_FALLBACK=chroma RAG_STORE_FALLBACK_UNTIL=2026-11-01

主存储抛异常时记日志 + 计数,改由兜底存储回答,并在 ``RAG_STORE_FALLBACK_COOLDOWN_S``
(默认 10)秒内跳过主存储直接走兜底(熔断:不应答的主存储每次都要等满 RPC 超时);过了 ``RAG_STORE_FALLBACK_UNTIL``
那一天(含当天仍有效)兜底自动失效并告警——避免两份索引长期不同步还"看起来能用"。
没写截止日期 = 不启用(强制限期)。兜底也失败时异常照常抛出,由
``rag.retrieve.query`` 退回关键词检索(那一层另有计数,见 ``/ready``)。

写操作(upsert / reset / seal)只发给主存储:影子和兜底都是只读陪跑。
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from rag.store.base import IMAGE_COLLECTION, TEXT_COLLECTION, Hit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SHADOW_LOG = REPO_ROOT / "data" / ".shadow" / "shadow.jsonl"


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.getenv(name, str(default)))
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def where_hash(where: dict | None) -> str:
    """过滤条件的短 hash:JSONL 里不落原始条件(可能很长),但同一条件可聚合。"""
    if not where:
        return ""
    raw = json.dumps(where, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def overlap_at_k(a: Sequence[str], b: Sequence[str], k: int) -> float:
    """两个结果前 k 名的重合比例(以两边实际条数的较大者为分母;两边都空记 1.0)。"""
    sa, sb = set(list(a)[:k]), set(list(b)[:k])
    denom = max(len(sa), len(sb))
    if denom == 0:
        return 1.0
    return len(sa & sb) / denom


def unwrap(store):
    """剥掉所有包装,返回真正干活的底层存储(就绪门控要绕过兜底直接查它)。"""
    seen = 0
    while hasattr(store, "primary") and seen < 8:
        store = store.primary
        seen += 1
    return store


class _Delegating:
    """把没覆盖的方法/属性全部转给主存储(``hasattr(store, "seal")`` 之类的探测也照常工作)。"""

    def __init__(self, primary) -> None:
        self.primary = primary

    @property
    def backend(self) -> str:
        return self.primary.backend

    def __getattr__(self, name: str):
        # 只有在本对象上找不到时才会走到这里;primary 还没设置时避免无限递归
        if name == "primary":
            raise AttributeError(name)
        return getattr(self.primary, name)


# ---------------------------------------------------------------------------
# 影子查询
# ---------------------------------------------------------------------------


class ShadowStore(_Delegating):
    def __init__(
        self,
        primary,
        shadow_factory: Callable[[], object],
        *,
        shadow_name: str = "",
        log_path: Path | str | None = None,
        timeout_ms: int | None = None,
        workers: int | None = None,
        queue_max: int | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        super().__init__(primary)
        self._shadow_factory = shadow_factory
        self._shadow_obj = None
        self._shadow_lock = threading.Lock()
        self.shadow_name = shadow_name
        self._log_path = Path(log_path or os.getenv("RAG_SHADOW_LOG") or DEFAULT_SHADOW_LOG)
        self._timeout_ms = timeout_ms if timeout_ms is not None else _env_int("RAG_SHADOW_TIMEOUT_MS", 2000, 1, 600_000)
        n_workers = workers if workers is not None else _env_int("RAG_SHADOW_WORKERS", 1, 1, 2)
        self._queue_max = queue_max if queue_max is not None else _env_int("RAG_SHADOW_QUEUE", 64, 1, 10_000)
        self._max_log_bytes = _env_int("RAG_SHADOW_LOG_MAX_MB", 200, 1, 100_000) * 1024 * 1024
        # 不用 ThreadPoolExecutor:它的工作线程不是 daemon,解释器退出时会 join 并把排队的
        # 任务全部跑完——影子库不应答时,64 个排队查询会把 systemctl restart 拖到被 SIGKILL。
        # 这里自己起 daemon 线程,进程退出时影子任务直接丢弃(本来就是可丢的旁路数据)。
        self._n_workers = max(1, min(2, n_workers))
        self._tasks: queue.SimpleQueue = queue.SimpleQueue()
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()
        # 在途(排队 + 执行中)任务的计数器:超过上限就丢弃,不阻塞主路径
        self._slots = threading.BoundedSemaphore(self._queue_max)
        self._write_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._clock = clock
        self.stats = {"submitted": 0, "dropped": 0, "logged": 0, "errors": 0, "timeouts": 0, "log_errors": 0}

    # -- 主路径 ---------------------------------------------------------------

    def query_text(self, embedding: Sequence[float], k: int = 5, *, where: dict | None = None) -> list[Hit]:
        return self._run(TEXT_COLLECTION, embedding, k, where)

    def query_image(self, embedding: Sequence[float], k: int = 5) -> list[Hit]:
        return self._run(IMAGE_COLLECTION, embedding, k, None)

    def _run(self, collection: str, embedding, k: int, where: dict | None) -> list[Hit]:
        t0 = self._clock()
        try:
            if collection == TEXT_COLLECTION:
                hits = self.primary.query_text(embedding, k, where=where)
            else:
                hits = self.primary.query_image(embedding, k)
        except Exception as exc:
            self._submit(collection, embedding, k, where, None, (self._clock() - t0) * 1000, f"{type(exc).__name__}: {exc}")
            raise
        self._submit(collection, embedding, k, where, [h.id for h in hits], (self._clock() - t0) * 1000, None)
        return hits

    def _submit(self, collection, embedding, k, where, primary_ids, primary_ms, primary_error) -> None:
        # 这里的任何异常都不能冒到主路径
        try:
            if not self._slots.acquire(blocking=False):
                with self._stats_lock:
                    self.stats["dropped"] += 1
                return
            try:
                vec = list(embedding)
                where = copy.deepcopy(where)  # 调用方之后改了这个 dict 也不影响后台比对
                self._ensure_workers()
                self._tasks.put((collection, vec, int(k), where, primary_ids, primary_ms, primary_error))
            except Exception:
                self._slots.release()
                with self._stats_lock:
                    self.stats["dropped"] += 1
                return
            with self._stats_lock:
                self.stats["submitted"] += 1
        except Exception:  # pragma: no cover - 防御
            pass

    # -- 后台 -----------------------------------------------------------------

    def _ensure_workers(self) -> None:
        if len(self._workers) >= self._n_workers:
            return
        with self._workers_lock:
            while len(self._workers) < self._n_workers:
                t = threading.Thread(
                    target=self._worker_loop, name=f"rag-shadow-{len(self._workers)}", daemon=True
                )
                t.start()
                self._workers.append(t)

    def _worker_loop(self) -> None:
        while True:
            task = self._tasks.get()
            self._shadow_task(*task)  # 自带兜底:不抛异常,finally 里归还名额

    def _shadow(self):
        if self._shadow_obj is None:
            with self._shadow_lock:
                if self._shadow_obj is None:
                    self._shadow_obj = self._shadow_factory()
        return self._shadow_obj

    def _shadow_task(self, collection, vec, k, where, primary_ids, primary_ms, primary_error) -> None:
        try:
            shadow_ids: list[str] | None = None
            error = None
            t0 = self._clock()
            try:
                store = self._shadow()
                if collection == TEXT_COLLECTION:
                    hits = store.query_text(vec, k, where=where)
                else:
                    hits = store.query_image(vec, k)
                shadow_ids = [h.id for h in hits]
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]
            shadow_ms = (self._clock() - t0) * 1000
            if error is None and shadow_ms > self._timeout_ms:
                error = f"timeout: {shadow_ms:.0f}ms > {self._timeout_ms}ms"
                with self._stats_lock:
                    self.stats["timeouts"] += 1
            elif error is not None:
                with self._stats_lock:
                    self.stats["errors"] += 1
            record = {
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
                "collection": collection,
                "k": k,
                "where_hash": where_hash(where),
                "primary": self.primary.backend,
                "shadow": self.shadow_name,
                "primary_ids": primary_ids,
                "shadow_ids": shadow_ids,
                "overlap_at_k": (
                    round(overlap_at_k(primary_ids, shadow_ids, k), 4)
                    if primary_ids is not None and shadow_ids is not None and not error
                    else None
                ),
                "primary_ms": round(primary_ms, 2),
                "shadow_ms": round(shadow_ms, 2),
                "error": error,
            }
            if primary_error:
                record["primary_error"] = primary_error[:500]
            self._append(record)
        except Exception:  # pragma: no cover - 防御:后台任务绝不让异常逃逸
            with self._stats_lock:
                self.stats["errors"] += 1
        finally:
            self._slots.release()

    def _append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with self._write_lock:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    size = self._log_path.stat().st_size
                except FileNotFoundError:
                    size = 0
                if size >= self._max_log_bytes:
                    # 只是防止磁盘被写满;48 小时影子正常流量远到不了这个量
                    with self._stats_lock:
                        self.stats["log_errors"] += 1
                    return
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            with self._stats_lock:
                self.stats["logged"] += 1
        except Exception:
            with self._stats_lock:
                self.stats["log_errors"] += 1

    # -- 运维 -----------------------------------------------------------------

    def drain(self, timeout: float = 10.0) -> bool:
        """等后台影子任务全部结束(测试 / 离线工具用;服务进程不需要调)。"""
        deadline = time.monotonic() + timeout
        taken = 0
        try:
            while taken < self._queue_max:
                left = deadline - time.monotonic()
                if left <= 0 or not self._slots.acquire(timeout=left):
                    return False
                taken += 1
            return True
        finally:
            for _ in range(taken):
                self._slots.release()

    def describe(self) -> dict:
        with self._stats_lock:
            stats = dict(self.stats)
        return {
            "shadow": self.shadow_name,
            "log": _display(self._log_path),
            "timeout_ms": self._timeout_ms,
            "queue_max": self._queue_max,
            **stats,
        }

    def schema_info(self) -> dict:
        info = dict(self.primary.schema_info())
        info["shadow"] = self.describe()
        return info


# ---------------------------------------------------------------------------
# 限期兜底
# ---------------------------------------------------------------------------


def parse_until(raw: str | None) -> _dt.date | None:
    if not raw or not raw.strip():
        return None
    try:
        return _dt.date.fromisoformat(raw.strip())
    except ValueError:
        return None


class FallbackStore(_Delegating):
    def __init__(
        self,
        primary,
        fallback_factory: Callable[[], object],
        *,
        fallback_name: str = "",
        until: _dt.date | None = None,
        today: Callable[[], _dt.date] = _dt.date.today,
        cooldown_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(primary)
        self._fallback_factory = fallback_factory
        self._fallback_obj = None
        self._lock = threading.Lock()
        self.fallback_name = fallback_name
        self.until = until
        self._today = today
        self._warned_expired = False
        # 熔断冷却:主存储失败一次后,接下来 cooldown 秒内直接由兜底回答、不再先试主存储。
        # 主存储"不应答"时每次都要等满 RPC 超时(Milvus 读超时默认 5 s)才失败,
        # 一次聊天请求里又有多次检索——不熔断的话整个故障期间每个请求都慢好几秒。
        # 冷却期过后放一个请求去试主存储,成功即恢复。0 = 不熔断。
        if cooldown_s is None:
            try:
                cooldown_s = float(os.getenv("RAG_STORE_FALLBACK_COOLDOWN_S", "10"))
            except ValueError:
                cooldown_s = 10.0
        self._cooldown_s = max(0.0, cooldown_s)
        self._clock = clock
        self._skip_primary_until = 0.0
        self.stats = {"primary_errors": 0, "served_by_fallback": 0, "fallback_errors": 0, "expired_skips": 0,
                      "primary_skipped_cooldown": 0}
        self.last_error: str | None = None

    def active(self) -> bool:
        return self.until is not None and self._today() <= self.until

    def _fallback(self):
        if self._fallback_obj is None:
            with self._lock:
                if self._fallback_obj is None:
                    self._fallback_obj = self._fallback_factory()
        return self._fallback_obj

    def _guard(self, call_primary, call_fallback):
        if self._cooldown_s and self._clock() < self._skip_primary_until and self.active():
            try:
                out = call_fallback(self._fallback())
            except Exception:
                with self._lock:
                    self.stats["fallback_errors"] += 1
                # 兜底也不行:冷却期内也还是去试一次主存储,别让两边都"假装"挂了
            else:
                with self._lock:
                    self.stats["primary_skipped_cooldown"] += 1
                    self.stats["served_by_fallback"] += 1
                return out
        try:
            return call_primary()
        except Exception as exc:
            with self._lock:
                self.stats["primary_errors"] += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                if self._cooldown_s:
                    self._skip_primary_until = self._clock() + self._cooldown_s
            if not self.active():
                with self._lock:
                    self.stats["expired_skips"] += 1
                    warn = not self._warned_expired
                    self._warned_expired = True
                if warn:
                    print(
                        f"[rag.store] WARNING: RAG_STORE_FALLBACK={self.fallback_name} expired "
                        f"(RAG_STORE_FALLBACK_UNTIL={self.until}); not falling back. Remove the setting.",
                        file=sys.stderr,
                    )
                raise
            print(
                f"[rag.store] primary {self.primary.backend} failed ({type(exc).__name__}: {exc}); "
                f"answering from fallback {self.fallback_name}"
                + (f" (primary skipped for {self._cooldown_s:g}s)" if self._cooldown_s else ""),
                file=sys.stderr,
            )
            try:
                out = call_fallback(self._fallback())
            except Exception:
                with self._lock:
                    self.stats["fallback_errors"] += 1
                raise exc from None
            with self._lock:
                self.stats["served_by_fallback"] += 1
            return out

    def query_text(self, embedding: Sequence[float], k: int = 5, *, where: dict | None = None) -> list[Hit]:
        return self._guard(
            lambda: self.primary.query_text(embedding, k, where=where),
            lambda fb: fb.query_text(embedding, k, where=where),
        )

    def query_image(self, embedding: Sequence[float], k: int = 5) -> list[Hit]:
        return self._guard(
            lambda: self.primary.query_image(embedding, k),
            lambda fb: fb.query_image(embedding, k),
        )

    def describe(self) -> dict:
        with self._lock:
            stats = dict(self.stats)
        return {
            "fallback": self.fallback_name,
            "until": self.until.isoformat() if self.until else None,
            "active": self.active(),
            "cooldown_s": self._cooldown_s,
            "primary_skipped_now": bool(self._cooldown_s and self._clock() < self._skip_primary_until),
            "last_error": self.last_error,
            **stats,
        }

    def schema_info(self) -> dict:
        info = dict(self.primary.schema_info())
        info["fallback"] = self.describe()
        return info


def wrapper_stats(store) -> dict:
    """沿包装链收集影子 / 兜底的计数,供 ``/ready`` 展示。"""
    out: dict = {}
    seen = 0
    while hasattr(store, "primary") and seen < 8:
        if isinstance(store, ShadowStore):
            out["shadow"] = store.describe()
        elif isinstance(store, FallbackStore):
            out["fallback"] = store.describe()
        store = store.primary
        seen += 1
    return out


def _display(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return Path(path).name


# ---------------------------------------------------------------------------
# 影子日志汇总:python -m rag.store.composite --summarize data/.shadow/shadow.jsonl
# ---------------------------------------------------------------------------


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(round(q / 100 * (len(s) - 1)))))], 2)


def summarize_shadow_log(path: Path | str) -> dict:
    """按 集合 × 是否带过滤 分组:条数、错误 / 超时、overlap@k 均值与 <1 的比例、两边 p50/p95。

    带过滤和不带过滤分开看:正向影子期间主存储如果还是线上的旧 Chroma 索引
    (没有 currency / brand_country 字段),带过滤的查询两边语义本就不同,overlap 低不代表 Milvus 有问题。
    """
    groups: dict[str, dict] = {}
    bad_lines = 0
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            bad_lines += 1
            continue
        key = f"{rec.get('collection')}|{'filtered' if rec.get('where_hash') else 'unfiltered'}"
        g = groups.setdefault(key, {"n": 0, "errors": 0, "timeouts": 0, "primary_errors": 0,
                                    "overlaps": [], "primary_ms": [], "shadow_ms": []})
        g["n"] += 1
        err = rec.get("error")
        if err:
            g["timeouts" if str(err).startswith("timeout") else "errors"] += 1
        if rec.get("primary_error"):
            g["primary_errors"] += 1
        if rec.get("overlap_at_k") is not None:
            g["overlaps"].append(float(rec["overlap_at_k"]))
        for side in ("primary_ms", "shadow_ms"):
            if rec.get(side) is not None:
                g[side].append(float(rec[side]))
    out: dict = {"file": str(path), "bad_lines": bad_lines, "groups": {}}
    for key, g in sorted(groups.items()):
        ov = g["overlaps"]
        out["groups"][key] = {
            "lines": g["n"], "shadow_errors": g["errors"], "shadow_timeouts": g["timeouts"],
            "primary_errors": g["primary_errors"],
            "overlap_mean": round(sum(ov) / len(ov), 4) if ov else None,
            "overlap_lt_1": round(sum(1 for x in ov if x < 1.0) / len(ov), 4) if ov else None,
            "primary_p50_ms": _pct(g["primary_ms"], 50), "primary_p95_ms": _pct(g["primary_ms"], 95),
            "shadow_p50_ms": _pct(g["shadow_ms"], 50), "shadow_p95_ms": _pct(g["shadow_ms"], 95),
        }
    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="summarise a ShadowStore JSONL log")
    ap.add_argument("--summarize", metavar="JSONL", default=str(DEFAULT_SHADOW_LOG))
    args = ap.parse_args(argv)
    path = Path(args.summarize)
    if not path.exists():
        print(f"no shadow log at {path}", file=sys.stderr)
        return 2
    print(json.dumps(summarize_shadow_log(path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
