"""向量库规模压测:Chroma vs Milvus(R15 P4)。

    python -m rag.bench.kuaisearch embed --n 1000000                 # 先算好向量(需要 torch)
    python -m rag.bench.scale --artifact data/.embeddings/kuaisearch_1m.npz --n 100000
    python -m rag.bench.scale --artifact data/.embeddings/kuaisearch_1m.npz --n 1000000

对每个后端测:
- 写入耗时(批量 upsert)与冷启动加载耗时(Milvus:重启服务后 load,含补建索引);
- 磁盘占用;稳定服务时的常驻内存(Chroma:独立查询进程;Milvus:服务进程);
- 5 档过滤(不过滤 / 宽 / 中 / 窄 / 品牌排除)下的查询延迟 p50/p95/p99,
  以及 recall@10(以 numpy 在同一批向量上暴力算出的精确解为准)。
Milvus 另跑一遍 ``RAG_MILVUS_FILTER_STRATEGY=plain``(固定 ef、不分档),
对照本仓库补的"按选择性分档"策略在大规模下的价值。

压测库在 ``data/.bench/``(已 gitignore),与生产索引完全隔离;
本进程与查询子进程都不加载 torch,Milvus 以独立服务进程运行。
结果摘要写到 ``docs/bench/``。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("CHROMA_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

BENCH_DIR = REPO_ROOT / "data" / ".bench"
OUT_DIR = REPO_ROOT / "docs" / "bench"
K = 60  # 生产稠密检索取 k*3 = 60
TOP = 10
BATCH = 5000


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else float("nan")


def _dir_mb(path: Path) -> float:
    out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True).stdout.split()
    return int(out[0]) / 1024 if out else float("nan")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_milvus(data_dir: Path, port: int) -> subprocess.Popen:
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "milvus_lite", "server", "--data-dir", str(data_dir), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return proc
        time.sleep(0.3)
    proc.kill()
    raise RuntimeError("milvus-lite server did not start")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _pct(xs: list[float], p: float) -> float:
    return float(np.percentile(np.asarray(xs), p)) if xs else float("nan")


# ---------------------------------------------------------------------------
# 数据与过滤条件
# ---------------------------------------------------------------------------

def load_prefix(artifact: Path, n: int):
    from rag.store.load import load_artifact

    ids, vecs, metas, texts, _ = load_artifact(artifact)
    n = min(n, len(ids))
    return ids[:n], np.ascontiguousarray(vecs[:n]), metas[:n], (texts[:n] if texts else None)


def pick_filters(metas: list[dict]) -> list[dict]:
    """按分布挑 5 档过滤条件;predicate 用于在 numpy 里算精确解。"""
    n = len(metas)
    cats = Counter(m["category"] for m in metas if m["category"])
    subs = Counter(m["sub_category"] for m in metas if m["sub_category"])
    brands = Counter(m["brand"] for m in metas)

    def closest(counter: Counter, share: float, min_count: int = 20) -> str:
        cands = [(abs(c / n - share), v) for v, c in counter.items() if c >= min_count]
        return min(cands)[1]

    broad = cats.most_common(1)[0][0]
    medium = closest(subs, 0.03)
    narrow = closest(subs, 0.001)
    top_brand = brands.most_common(1)[0][0]
    return [
        {"name": "none", "where": None, "field": None},
        {"name": "broad", "where": {"category": broad}, "field": ("category", "eq", broad)},
        {"name": "medium", "where": {"sub_category": {"$in": [medium]}}, "field": ("sub_category", "eq", medium)},
        {"name": "narrow", "where": {"sub_category": {"$in": [narrow]}}, "field": ("sub_category", "eq", narrow)},
        {"name": "exclude", "where": {"brand": {"$nin": [top_brand]}}, "field": ("brand", "ne", top_brand)},
    ]


def _mask(metas: list[dict], field) -> np.ndarray:
    if field is None:
        return np.ones(len(metas), dtype=bool)
    key, op, val = field
    col = np.asarray([m.get(key, "") for m in metas], dtype=object)
    return (col == val) if op == "eq" else (col != val)


def exact_topk(vecs: np.ndarray, mask: np.ndarray, queries: np.ndarray, k: int, ids: list[str]) -> list[list[str]]:
    idx = np.flatnonzero(mask)
    sub = vecs[idx]
    out: list[list[str]] = []
    for s in range(0, len(queries), 32):
        sims = queries[s : s + 32] @ sub.T
        kk = min(k, sims.shape[1])
        part = np.argpartition(-sims, kk - 1, axis=1)[:, :kk] if kk else np.zeros((len(sims), 0), int)
        for row, cand in zip(sims, part):
            order = cand[np.argsort(-row[cand], kind="stable")]
            out.append([ids[idx[j]] for j in order])
    return out


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _docs(ids, metas, texts, s, e):
    from rag.store import Doc

    return [Doc(id=ids[i], text=(texts[i] if texts else metas[i].get("text", "")), metadata=metas[i]) for i in range(s, e)]


def ingest(store, ids, vecs, metas, texts) -> float:
    from rag.store import TEXT_COLLECTION

    store.reset_collection(TEXT_COLLECTION, properties={"dataset": "kuaisearch", "n": str(len(ids))})
    t0 = time.perf_counter()
    for s in range(0, len(ids), BATCH):
        e = min(s + BATCH, len(ids))
        store.upsert_text(_docs(ids, metas, texts, s, e), vecs[s:e].tolist())
        if (e // BATCH) % 20 == 0 or e == len(ids):
            el = time.perf_counter() - t0
            print(f"    {store.backend}: {e:,}/{len(ids):,}  ({e / el:,.0f}/s)", flush=True)
    if hasattr(store, "seal"):
        store.seal(TEXT_COLLECTION)
    return time.perf_counter() - t0


# ---------------------------------------------------------------------------
# 查询(在独立子进程里跑,测稳定服务状态)
# ---------------------------------------------------------------------------

def cmd_serve(args) -> int:
    from rag.store import TEXT_COLLECTION

    q = np.load(args.queries)["vectors"].astype(np.float32)[: args.nq]
    filters = json.loads(Path(args.filters).read_text(encoding="utf-8"))
    if args.backend == "chroma":
        from rag.store.chroma_store import ChromaStore

        store = ChromaStore(args.path)
    else:
        from rag.store.milvus_store import MilvusStore

        store = MilvusStore(uri=args.uri, index_type="HNSW")
    t0 = time.perf_counter()
    n = store.text_count()
    load_s = time.perf_counter() - t0
    for v in q[: args.warmup]:  # 预热
        store.query_text(v.tolist(), K)
    res = {"count": n, "first_touch_s": load_s, "latency_ms": {}, "ids": {}, "returned": {}, "paths": {}}
    for f in filters:
        lat, got, ret, paths = [], [], [], Counter()
        for v in q:
            t = time.perf_counter()
            hits = store.query_text(v.tolist(), K, where=f["where"])
            lat.append((time.perf_counter() - t) * 1000)
            got.append([h.id for h in hits[:TOP]])
            ret.append(len(hits))
            if hasattr(store, "last_search_path"):
                paths[store.last_search_path.split("(")[0]] += 1
        print(f"    serve {args.backend}: {f['name']:<8} p50 {_pct(lat, 50):,.0f} ms over {len(lat)} queries", flush=True)
        res["latency_ms"][f["name"]] = lat
        res["ids"][f["name"]] = got
        res["returned"][f["name"]] = ret
        res["paths"][f["name"]] = dict(paths)
    res["rss_mb"] = _rss_mb(os.getpid())
    Path(args.out).write_text(json.dumps(res), encoding="utf-8")
    return 0


def _serve(backend: str, *, path=None, uri=None, qfile: Path, ffile: Path, nq: int, env_extra=None, warmup: int = 20) -> dict:
    out = BENCH_DIR / f"serve_{backend}_{os.getpid()}_{int(time.time() * 1000)}.json"
    cmd = [sys.executable, "-m", "rag.bench.scale", "serve", "--backend", backend, "--queries", str(qfile),
           "--filters", str(ffile), "--nq", str(nq), "--out", str(out), "--warmup", str(warmup)]
    cmd += ["--path", str(path)] if path else ["--uri", uri]
    env = {**os.environ, **(env_extra or {}), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"serve {backend} failed (rc={proc.returncode})")
    res = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)
    return res


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _summarize(res: dict, truth: dict, filters: list[dict]) -> dict:
    rows = {}
    for f in filters:
        name = f["name"]
        got = res["ids"][name]
        tr = truth[name]
        recalls = [len(set(g) & set(t[:TOP])) / max(1, min(TOP, len(t))) for g, t in zip(got, tr[: len(got)])]
        lat = res["latency_ms"][name]
        rows[name] = {
            "p50_ms": round(_pct(lat, 50), 2),
            "p95_ms": round(_pct(lat, 95), 2),
            "p99_ms": round(_pct(lat, 99), 2),
            "recall@10": round(float(np.mean(recalls)), 4),
            "min_recall@10": round(float(np.min(recalls)), 2),
            "avg_returned": round(float(np.mean(res["returned"][name])), 1),
            "paths": res.get("paths", {}).get(name, {}),
        }
    return rows


def cmd_run(args) -> int:
    from rag.store.chroma_store import ChromaStore
    from rag.store.milvus_store import MilvusStore

    from rag.bench.kuaisearch import tag_for

    ids, vecs, metas, texts = load_prefix(Path(args.artifact), args.n)
    n = len(ids)
    tag = tag_for(n)
    print(f"[scale] {n:,} vectors × {vecs.shape[1]} dims ({vecs.nbytes / 1e9:.2f} GB raw float32)")
    qfile = Path(args.queries)
    queries = np.load(qfile)["vectors"].astype(np.float32)[: args.nq]
    filters = pick_filters(metas)
    ffile = BENCH_DIR / f"filters_{tag}.json"
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    ffile.write_text(json.dumps([{"name": f["name"], "where": f["where"]} for f in filters], ensure_ascii=False), encoding="utf-8")

    t0 = time.perf_counter()
    truth, sel = {}, {}
    for f in filters:
        m = _mask(metas, f["field"])
        sel[f["name"]] = float(m.mean())
        truth[f["name"]] = exact_topk(vecs, m, queries, TOP, ids)
    print(f"[scale] exact ground truth for {len(queries)} queries × {len(filters)} filters in {time.perf_counter() - t0:.1f}s")
    for f in filters:
        print(f"    {f['name']:<8} selectivity {sel[f['name']]:7.3%}  where={json.dumps(f['where'], ensure_ascii=False)}")

    import rag.store.milvus_store as _ms

    report = {"dataset": "KuaiSearch items_lite (first N, file order)", "n": n, "dim": int(vecs.shape[1]),
              "milvus_params": {"hnsw_ef": _ms._hnsw_ef(0), "filter_brute_max": _ms._BRUTE_MAX,
                                "filter_ef_factor": _ms._EF_FACTOR, "hnsw_ef_max": _ms._EF_MAX,
                                "index": "HNSW M=16 efConstruction=200", "mode": "milvus-lite 3.2.1 server"},
              "machine": "MacBook Air M4 (Mac16,12), 24 GB",
              "queries": len(queries), "k": K, "filters": [{"name": f["name"], "where": f["where"],
              "selectivity": round(sel[f["name"]], 5)} for f in filters], "backends": {}}
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]

    if "chroma" in backends:
        path = BENCH_DIR / f"chroma_{tag}"
        side = BENCH_DIR / f"chroma_{tag}.ingest.json"
        if args.reuse and path.exists() and side.exists():
            t_ing = json.loads(side.read_text())["ingest_s"]
            print(f"[scale] chroma: reuse existing store (ingest was {t_ing:.0f}s)")
        else:
            shutil.rmtree(path, ignore_errors=True)
            print("[scale] chroma: ingest", flush=True)
            t_ing = ingest(ChromaStore(path), ids, vecs, metas, texts)
            side.write_text(json.dumps({"ingest_s": round(t_ing, 1)}))
        disk = _dir_mb(path)
        print(f"[scale] chroma: ingest {t_ing:.1f}s, disk {disk:,.0f} MB; serving …", flush=True)
        cnq = min(len(queries), args.chroma_nq or len(queries))
        res = _serve("chroma", path=path, qfile=qfile, ffile=ffile, nq=cnq, warmup=min(20, max(2, cnq // 5)))
        report["backends"]["chroma"] = {"ingest_s": round(t_ing, 1), "cold_load_s": round(res["first_touch_s"], 2),
                                        "disk_mb": round(disk), "serving_rss_mb": round(res["rss_mb"]), "queries": cnq,
                                        "filters": _summarize(res, truth, filters)}
        if not args.keep:
            shutil.rmtree(path, ignore_errors=True)

    if "milvus" in backends:
        data_dir = BENCH_DIR / f"milvus_{tag}"
        side = BENCH_DIR / f"milvus_{tag}.ingest.json"
        port = _free_port()
        uri = f"http://127.0.0.1:{port}"
        if args.reuse and data_dir.exists() and side.exists():
            meta_ing = json.loads(side.read_text())
            t_ing = meta_ing["ingest_s"]
            ingest_rss = meta_ing.get("server_rss_after_ingest_mb") or float("nan")
            print(f"[scale] milvus: reuse existing store (ingest was {t_ing:.0f}s)", flush=True)
        else:
            shutil.rmtree(data_dir, ignore_errors=True)
            srv = _start_milvus(data_dir, port)
            try:
                print("[scale] milvus: ingest", flush=True)
                t_ing = ingest(MilvusStore(uri=uri, index_type="HNSW"), ids, vecs, metas, texts)
                ingest_rss = _rss_mb(srv.pid)
            finally:
                _stop(srv)  # 干净关闭:数据落盘
            side.write_text(json.dumps({"ingest_s": round(t_ing, 1),
                                        "server_rss_after_ingest_mb": (round(ingest_rss) if ingest_rss == ingest_rss else None)}))
        disk = _dir_mb(data_dir)
        srv = _start_milvus(data_dir, port)  # 冷启动:集合以 released 打开,首次 load 会补建缺的索引
        try:
            t = time.perf_counter()
            st = MilvusStore(uri=uri, index_type="HNSW")
            st._ensure_loaded("products_text")
            cold = time.perf_counter() - t
            print(f"[scale] milvus: ingest {t_ing:.1f}s, cold load {cold:.1f}s, disk {disk:,.0f} MB; serving …", flush=True)
            res = _serve("milvus", uri=uri, qfile=qfile, ffile=ffile, nq=len(queries))
            res_plain = _serve("milvus", uri=uri, qfile=qfile, ffile=ffile, nq=len(queries),
                               env_extra={"RAG_MILVUS_FILTER_STRATEGY": "plain"})
            srv_rss = _rss_mb(srv.pid)
        finally:
            _stop(srv)
        report["backends"]["milvus"] = {"ingest_s": round(t_ing, 1), "cold_load_s": round(cold, 1),
                                        "disk_mb": round(disk), "server_rss_mb": round(srv_rss),
                                        "server_rss_after_ingest_mb": (round(ingest_rss) if ingest_rss == ingest_rss else None),
                                        "filters": _summarize(res, truth, filters),
                                        "filters_plain_ef": _summarize(res_plain, truth, filters)}
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"kuaisearch_{tag}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_report(report)
    print(f"\n[scale] saved {out.relative_to(REPO_ROOT)}")
    return 0


def cmd_calibrate(args) -> int:
    """只测 Milvus:固定 ef 的若干档 vs 全部走精确计算,看每档过滤的 recall / 延迟曲线。
    用来给 milvus_store 的分档阈值找依据,而不是拍脑袋。"""
    from rag.store.milvus_store import MilvusStore

    from rag.bench.kuaisearch import tag_for

    ids, vecs, metas, texts = load_prefix(Path(args.artifact), args.n)
    tag = tag_for(len(ids))
    qfile = Path(args.queries)
    queries = np.load(qfile)["vectors"].astype(np.float32)[: args.nq]
    filters = pick_filters(metas)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    ffile = BENCH_DIR / f"filters_{tag}.json"
    ffile.write_text(json.dumps([{"name": f["name"], "where": f["where"]} for f in filters], ensure_ascii=False), encoding="utf-8")
    truth, sel, matched = {}, {}, {}
    for f in filters:
        m = _mask(metas, f["field"])
        sel[f["name"]], matched[f["name"]] = float(m.mean()), int(m.sum())
        truth[f["name"]] = exact_topk(vecs, m, queries, TOP, ids)

    data_dir = BENCH_DIR / f"milvus_{tag}"
    port = _free_port()
    uri = f"http://127.0.0.1:{port}"
    if not (data_dir.exists() and args.reuse):
        shutil.rmtree(data_dir, ignore_errors=True)
        srv = _start_milvus(data_dir, port)
        try:
            ingest(MilvusStore(uri=uri, index_type="HNSW"), ids, vecs, metas, texts)
        finally:
            _stop(srv)
    srv = _start_milvus(data_dir, port)
    configs = [(f"固定 ef={ef}", {"RAG_MILVUS_FILTER_STRATEGY": "plain", "RAG_MILVUS_HNSW_EF": str(ef)})
               for ef in (1024, 4096, 16384, 65536)]
    configs.append(("全部精确计算", {"RAG_MILVUS_FILTER_STRATEGY": "adaptive", "RAG_MILVUS_FILTER_BRUTE_MAX": "1000000000"}))
    configs.append(("分档策略(默认参数)", {"RAG_MILVUS_FILTER_STRATEGY": "adaptive"}))
    rows = {}
    try:
        MilvusStore(uri=uri, index_type="HNSW")._ensure_loaded("products_text")
        for label, env in configs:
            res = _serve("milvus", uri=uri, qfile=qfile, ffile=ffile, nq=len(queries), env_extra=env)
            rows[label] = _summarize(res, truth, filters)
            print(f"  done: {label}", flush=True)
    finally:
        _stop(srv)
    print(f"\n# 校准:{len(ids):,} 条,{len(queries)} 条 query,k={K}(每格:recall@10 / p50)\n")
    print("| 过滤 | 选择性 | 合格条数 | " + " | ".join(l for l, _ in configs) + " |")
    print("|---|---:|---:|" + "---|" * len(configs))
    for f in filters:
        n_ = f["name"]
        cells = [f"{rows[l][n_]['recall@10']:.3f} / {rows[l][n_]['p50_ms']:.0f} ms" for l, _ in configs]
        print(f"| {n_} | {sel[n_]:.2%} | {matched[n_]:,} | " + " | ".join(cells) + " |")
    out = OUT_DIR / f"calibrate_{tag}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"n": len(ids), "queries": len(queries), "selectivity": sel, "matched": matched,
                               "results": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[calibrate] saved {out.relative_to(REPO_ROOT)}")
    if not args.keep:
        shutil.rmtree(data_dir, ignore_errors=True)
    return 0


def merge_reports(paths: list[Path], out: Path) -> dict:
    """把分开跑的单后端报告合成一份(同一数据、同一批 query 与过滤条件)。"""
    merged: dict | None = None
    for p in paths:
        r = json.loads(Path(p).read_text(encoding="utf-8"))
        if merged is None:
            merged = r
        else:
            assert r["n"] == merged["n"] and r["filters"] == merged["filters"], f"incompatible report {p}"
            merged["backends"].update(r["backends"])
            merged["queries"] = max(merged["queries"], r["queries"])
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return merged


def print_report(r: dict) -> None:
    print(f"\n# 规模压测:{r['n']:,} 条 × {r['dim']} 维,{r['queries']} 条 query,k={r['k']}\n")
    print("| 后端 | 写入 | 冷启动加载 | 磁盘 | 常驻内存 |")
    print("|---|---:|---:|---:|---:|")
    for b, v in r["backends"].items():
        mem = v.get("serving_rss_mb", v.get("server_rss_mb"))
        print(f"| {b} | {v['ingest_s']:,.0f} s | {v['cold_load_s']:,.1f} s | {v['disk_mb']:,} MB | {mem:,} MB |")
    sel = {f["name"]: f["selectivity"] for f in r["filters"]}
    print("\n| 过滤 | 选择性 | 后端 | p50 | p95 | p99 | recall@10 | 最低 recall@10 |")
    print("|---|---:|---|---:|---:|---:|---:|---:|")
    for f in r["filters"]:
        name = f["name"]
        for b, v in r["backends"].items():
            for label, key in ((b, "filters"), (f"{b}(固定 ef)", "filters_plain_ef")):
                if key not in v:
                    continue
                x = v[key][name]
                print(f"| {name} | {sel[name]:.2%} | {label} | {x['p50_ms']:.1f} ms | {x['p95_ms']:.1f} ms | "
                      f"{x['p99_ms']:.1f} ms | {x['recall@10']:.3f} | {x['min_recall@10']:.2f} |")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--backend", choices=("chroma", "milvus"), required=True)
    s.add_argument("--path")
    s.add_argument("--uri")
    s.add_argument("--queries", required=True)
    s.add_argument("--filters", required=True)
    s.add_argument("--nq", type=int, default=300)
    s.add_argument("--out", required=True)
    s.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--artifact", default=str(REPO_ROOT / "data" / ".embeddings" / "kuaisearch_1m.npz"))
    ap.add_argument("--queries", default=str(REPO_ROOT / "data" / ".embeddings" / "kuaisearch_queries.npz"))
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--nq", type=int, default=300)
    ap.add_argument("--backends", default="chroma,milvus")
    ap.add_argument("--chroma-nq", type=int, default=0, help="cap Chroma's query count (0 = same as --nq)")
    ap.add_argument("--keep", action="store_true", help="keep the bench stores in data/.bench/")
    ap.add_argument("--calibrate", action="store_true", help="Milvus only: recall/latency vs ef and vs exact")
    ap.add_argument("--reuse", action="store_true", help="reuse existing data/.bench stores (skip ingest; ingest time read from the sidecar)")
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.calibrate:
        return cmd_calibrate(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
