"""回放门槛:切换向量库之前,两个后端 + numpy 精确解在同一批向量上逐条对拍(P1 第 3 步)。

    # 前提:B 是 A 用 rag.store.migrate 原样搬过去的(向量逐位相同),两边都是 schema v2
    RAG_MILVUS_URI=http://127.0.0.1:19530 python -m rag.eval.replay_gate --label vm-20261010
    python -m rag.eval.replay_gate --a chroma --b milvus --b-uri http://127.0.0.1:19530 --min-calls 1500

回放的查询(每条都同时发给 A、B,并用 numpy 在 A 导出的全部向量上暴力算精确 top-k):

* golden + compositional 单轮/多轮用例(与 ``rag.eval.store_parity`` 相同),每条两种过滤:
  不过滤,以及生产路径实际会用的过滤(对话状态过滤,否则 ``build_retrieval_filter``);
* ``tools/stress_e2e.py`` 正确性用例里的查询(能 import 到时);
* 145 张商品图各自的图片向量查 image 集合。

一轮不够 ``--min-calls``(默认 1000)次就整轮重放,直到够数——重复轮次同时检查
B 的结果是否确定(同一查询前后两次结果不同会单独计数,不算失败)。

判定(退出码非 0 即不通过):

* A、B 的 text / image 条数不一致 → 失败(B 应当是 A 原样迁移的);
* 任一后端任一次调用报错 → 失败;
* top-10 一致率:两边前 10 名集合相同(第 10 名分数并列造成的差异不算不一致,见
  ``store_parity._topk_agree``);不一致的那些,如果 B 对精确解的 recall@10 与 recall@K
  都不低于 A、且共同命中的分数一致,就算"被精确解解释"(B 至少和 A 一样接近标准答案);
* (一致 + 被解释) / 总调用数 < ``--min-agreement``(默认 0.99)→ 失败;
* 可选 ``--p95-max-ms``:B 的 p95 超过它 → 失败(默认只报告,PLAN 说按实测校准)。

报告写到 ``docs/bench/replay_gate_<label>.json``。注意:本地 Milvus Lite 的延迟数字
不能当成生产 Standalone 的结论,报告里会记下 URI 与模式。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("CHROMA_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from rag.eval.store_parity import (  # noqa: E402  复用对拍逻辑,保证两份工具口径一致
    K,
    SCORE_EPS,
    TOP,
    Exact,
    _cases,
    _recall,
    _topk_agree,
)

OUT_DIR = REPO_ROOT / "docs" / "bench"
EPS = 1e-9


# ---------------------------------------------------------------------------
# 查询集
# ---------------------------------------------------------------------------


def _stress_cases() -> list[dict]:
    """tools/stress_e2e.py 正确性用例的消息,转成与 golden 相同的 case 结构。"""
    tools = REPO_ROOT / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import stress_e2e  # type: ignore

        cases = stress_e2e._build_correctness_cases()
    except Exception as exc:  # 没有这个脚本或接口变了:跳过,报告里记一笔
        print(f"[replay] stress_e2e cases unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    out = []
    for c in cases:
        msgs = [m for m in getattr(c, "messages", []) if m.get("content")]
        if not msgs:
            continue
        case = {"messages": msgs} if len(msgs) > 1 else {"query": msgs[0]["content"]}
        case["_set"] = "stress_e2e"
        out.append(case)
    return out


def build_text_calls() -> list[dict]:
    """每个元素是一次文本检索调用:{set, query, filter, where}。"""
    from rag.eval.core import case_query, case_retrieval_state
    from rag.retrieve.constraints import build_retrieval_filter
    from rag.retrieve.query import Filter, _build_where

    calls = []
    for case in _cases() + _stress_cases():
        text = case_query(case)
        if not text or not text.strip():
            continue
        conv, _ = case_retrieval_state(case)
        f = conv if isinstance(conv, Filter) else build_retrieval_filter(text)
        calls.append({"set": case["_set"], "query": text, "filter": "none", "where": None})
        where = _build_where(f)
        if where:
            calls.append({"set": case["_set"], "query": text, "filter": "prod", "where": where})
    return calls


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, int(round(q / 100 * (len(s) - 1)))))
    return round(s[idx], 2)


def _lat(xs: list[float]) -> dict:
    return {"n": len(xs), "p50_ms": _pct(xs, 50), "p95_ms": _pct(xs, 95), "p99_ms": _pct(xs, 99),
            "mean_ms": round(statistics.fmean(xs), 2) if xs else None}


def _compare(a: list[tuple[str, float]], b: list[tuple[str, float]], e: list[tuple[str, float]], top: int, k: int) -> dict:
    agree = _topk_agree(a, b, top)
    common = {i for i, _ in a} & {i for i, _ in b}
    da, db = dict(a), dict(b)
    score_diff = max((abs(da[i] - db[i]) for i in common), default=0.0)
    ra10, rb10 = _recall(a, e, top), _recall(b, e, top)
    raK, rbK = _recall(a, e, k), _recall(b, e, k)
    explained = (not agree) and rb10 >= ra10 - EPS and rbK >= raK - EPS and score_diff <= SCORE_EPS
    return {
        "agree": agree,
        "explained": explained,
        "score_diff": score_diff,
        "a_recall10": ra10, "b_recall10": rb10, "a_recallK": raK, "b_recallK": rbK,
        "b_better": rb10 > ra10 + EPS or rbK > raK + EPS,
    }


def _store(name: str, uri: str | None):
    from rag.store import get_store

    if name == "milvus" and uri:
        from rag.store.milvus_store import MilvusStore

        return MilvusStore(uri=uri)
    return get_store(name)


def _describe(store) -> dict:
    try:
        info = store.schema_info()
    except Exception as exc:
        return {"backend": getattr(store, "backend", "?"), "error": str(exc)}
    keep = {k: info.get(k) for k in ("backend", "mode", "uri", "path", "index_type", "versioned")}
    for label in ("text", "image"):
        entry = info.get(label) or {}
        keep[label] = {k: entry.get(k) for k in ("count", "schema_version", "physical") if k in entry}
    return {k: v for k, v in keep.items() if v is not None}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(args) -> int:
    from rag.store import IMAGE_COLLECTION, TEXT_COLLECTION

    a = _store(args.a, args.a_uri)
    b = _store(args.b, args.b_uri)
    if args.a == args.b and (args.a_uri or "") == (args.b_uri or ""):
        print("A and B are the same store", file=sys.stderr)
        return 2
    for s in (a, b):
        # macOS:本进程要加载 torch(嵌入模型),嵌入式 Milvus Lite + faiss 索引在这里搜索会 abort
        if getattr(s, "write_needs_isolation", None) and s.write_needs_isolation():
            print(f"{s.backend}: embedded Milvus Lite with a faiss index cannot run next to torch on macOS; "
                  "use server mode (tools/milvus-lite-server.sh + --b-uri http://127.0.0.1:19530)", file=sys.stderr)
            return 2
        if "brand_country" not in s.filterable_fields(TEXT_COLLECTION):
            print(f"{s.backend} text index is pre-v2; rebuild + migrate first (see docs/RUNBOOK_MILVUS.md)",
                  file=sys.stderr)
            return 2

    from rag.ingest.embed_text import embed_query

    print("[replay] exporting vectors from A for exact search …", file=sys.stderr)
    exact_text = Exact(a, TEXT_COLLECTION)
    exact_img = Exact(a, IMAGE_COLLECTION) if not args.skip_images else None
    counts = {"a_text": a.text_count(), "b_text": b.text_count(),
              "a_image": a.image_count(), "b_image": b.image_count()}
    # 条数不一致直接判失败:B 应当是 A 原样搬过去的。B 多出来的文档精确解(只看 A 的向量)
    # 根本看不到,"被精确解解释"的判断在这种情况下不成立。
    size_mismatch = counts["a_text"] != counts["b_text"] or (
        exact_img is not None and counts["a_image"] != counts["b_image"]
    )
    if size_mismatch:
        print(f"[replay] collection sizes differ: {counts}", file=sys.stderr)

    text_calls = build_text_calls()
    vec_cache: dict[str, list[float]] = {}
    for c in text_calls:
        if c["query"] not in vec_cache:
            vec_cache[c["query"]] = embed_query(c["query"])
    img_calls = []
    if exact_img is not None:
        for pid, n in exact_img.by_id.items():
            img_calls.append({"set": "images", "query": pid, "filter": "none", "where": None,
                              "vec": exact_img.mat[n].tolist()})
    per_round = len(text_calls) + len(img_calls)
    if per_round == 0:
        print("no calls to replay", file=sys.stderr)
        return 2
    rounds = max(1, -(-args.min_calls // per_round))
    print(f"[replay] {len(text_calls)} text + {len(img_calls)} image calls per round × {rounds} round(s)",
          file=sys.stderr)

    lat = {"a": {"text": [], "image": []}, "b": {"text": [], "image": []}, "exact": {"text": [], "image": []}}
    rows: list[dict] = []
    errors: list[dict] = []
    exact_cache: dict[int, list[tuple[str, float]]] = {}
    first_b: dict[int, list[str]] = {}
    nondeterministic = 0
    t_start = time.perf_counter()

    for rnd in range(rounds):
        for idx, call in enumerate(text_calls + img_calls):
            coll = "image" if call["set"] == "images" else "text"
            vec = call["vec"] if coll == "image" else vec_cache[call["query"]]
            k = TOP if coll == "image" else args.k
            res: dict[str, list[tuple[str, float]] | None] = {}
            # 交替先后顺序,避免谁先查谁吃缓存预热的系统性偏差
            order = (("a", a), ("b", b)) if (idx + rnd) % 2 == 0 else (("b", b), ("a", a))
            for label, store in order:
                t0 = time.perf_counter()
                try:
                    if coll == "image":
                        hits = store.query_image(vec, k)
                    else:
                        hits = store.query_text(vec, k, where=call["where"])
                    res[label] = [(h.id, h.score) for h in hits]
                except Exception as exc:
                    res[label] = None
                    errors.append({"store": label, "backend": store.backend, "set": call["set"],
                                   "query": call["query"][:60], "filter": call["filter"],
                                   "error": f"{type(exc).__name__}: {exc}"[:300]})
                lat[label][coll].append((time.perf_counter() - t0) * 1000)
            if idx not in exact_cache:
                t0 = time.perf_counter()
                ex = (exact_img if coll == "image" else exact_text).topk(vec, k, call["where"])
                lat["exact"][coll].append((time.perf_counter() - t0) * 1000)
                exact_cache[idx] = ex
            row = {"round": rnd, "set": call["set"], "collection": coll, "filter": call["filter"],
                   "query": call["query"][:40], "where": json.dumps(call["where"], ensure_ascii=False) if call["where"] else ""}
            if res["a"] is None or res["b"] is None:
                row.update({"error": True, "agree": False, "explained": False})
            else:
                row.update(_compare(res["a"], res["b"], exact_cache[idx], TOP, k))
                b_ids = [i for i, _ in res["b"]]
                if idx in first_b and first_b[idx] != b_ids:
                    nondeterministic += 1
                first_b.setdefault(idx, b_ids)
            rows.append(row)

    total = len(rows)
    n_err_rows = sum(1 for r in rows if r.get("error"))
    agree = sum(1 for r in rows if r.get("agree"))
    explained = sum(1 for r in rows if r.get("explained"))
    unexplained_rows = [r for r in rows if not r.get("agree") and not r.get("explained")]
    agreement = agree / total
    effective = (agree + explained) / total

    def slice_stats(rs: list[dict]) -> dict:
        ok = [r for r in rs if not r.get("error")]
        return {
            "calls": len(rs),
            "top10_agreement": round(sum(1 for r in rs if r.get("agree")) / max(1, len(rs)), 4),
            "explained_by_exact": sum(1 for r in rs if r.get("explained")),
            "unexplained": sum(1 for r in rs if not r.get("agree") and not r.get("explained")),
            "a_recall10_vs_exact": round(statistics.fmean(r["a_recall10"] for r in ok), 4) if ok else None,
            "b_recall10_vs_exact": round(statistics.fmean(r["b_recall10"] for r in ok), 4) if ok else None,
            "max_score_diff": max((r["score_diff"] for r in ok), default=0.0),
        }

    slices = {}
    for key, pred in (
        ("text_none", lambda r: r["collection"] == "text" and r["filter"] == "none"),
        ("text_prod_filter", lambda r: r["collection"] == "text" and r["filter"] == "prod"),
        ("text_stress_e2e", lambda r: r["set"] == "stress_e2e"),
        ("image", lambda r: r["collection"] == "image"),
    ):
        rs = [r for r in rows if pred(r)]
        if rs:
            slices[key] = slice_stats(rs)

    reasons = []
    if size_mismatch:
        reasons.append(f"collection sizes differ: {counts}")
    if errors:
        reasons.append(f"{len(errors)} store errors")
    if effective < args.min_agreement:
        reasons.append(f"agreement incl. exact-explained {effective:.4f} < {args.min_agreement}")
    b_p95 = _pct(lat["b"]["text"] + lat["b"]["image"], 95)
    if args.p95_max_ms is not None and b_p95 is not None and b_p95 > args.p95_max_ms:
        reasons.append(f"B p95 {b_p95}ms > {args.p95_max_ms}ms")

    from rag.retrieve.query import _filter_pushdown_on

    report = {
        "label": args.label,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "platform": f"{sys.platform}-{os.uname().machine}" if hasattr(os, "uname") else sys.platform,
        "a": {"name": args.a, **_describe(a)},
        "b": {"name": args.b, **_describe(b)},
        "config": {"k_text": args.k, "top": TOP, "min_calls": args.min_calls, "rounds": rounds,
                   "min_agreement": args.min_agreement, "p95_max_ms": args.p95_max_ms,
                   "filter_pushdown": _filter_pushdown_on(), "score_eps": SCORE_EPS},
        "counts": {**counts, "calls": total, "text_calls_per_round": len(text_calls),
                   "image_calls_per_round": len(img_calls), "unique_text_queries": len(vec_cache)},
        "errors": {"count": len(errors), "calls_with_errors": n_err_rows, "sample": errors[:20]},
        "agreement": {
            "top10_agreement": round(agreement, 4),
            "explained_by_exact": explained,
            "unexplained": len(unexplained_rows),
            "agreement_incl_explained": round(effective, 4),
            "b_better_than_a_vs_exact": sum(1 for r in rows if r.get("b_better")),
            "b_nondeterministic_repeats": nondeterministic,
            "unexplained_sample": unexplained_rows[:20],
        },
        "slices": slices,
        "latency_ms": {
            store: {coll: _lat(xs) for coll, xs in by.items()} | {"all": _lat(by["text"] + by["image"])}
            for store, by in lat.items()
        },
        "wall_s": round(time.perf_counter() - t_start, 1),
        "verdict": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
    }

    print(f"\n# 回放门槛:{args.a} (A) vs {args.b} (B) · {total} 次调用 · K={args.k}\n")
    print("| 切片 | 调用 | top10 一致 | 被精确解解释 | 未解释 | A recall@10 | B recall@10 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for key, st in slices.items():
        print(f"| {key} | {st['calls']} | {st['top10_agreement']:.4f} | {st['explained_by_exact']} | "
              f"{st['unexplained']} | {st['a_recall10_vs_exact']} | {st['b_recall10_vs_exact']} |")
    la, lb = report["latency_ms"]["a"]["all"], report["latency_ms"]["b"]["all"]
    print(f"\n错误 {len(errors)} · 一致率 {agreement:.4f} · 含精确解解释 {effective:.4f} · "
          f"B 重复查询结果变化 {nondeterministic} 次")
    print(f"延迟 A p50/p95 {la['p50_ms']}/{la['p95_ms']} ms · B p50/p95 {lb['p50_ms']}/{lb['p95_ms']} ms "
          f"(B: {report['b'].get('mode', report['b'].get('backend'))} {report['b'].get('uri', '')})")
    print(f"结论:{report['verdict']}" + (f"  ({'; '.join(reasons)})" if reasons else ""))

    if not args.no_write:
        out = Path(args.out) if args.out else OUT_DIR / f"replay_gate_{args.label}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            shown = out.resolve().relative_to(REPO_ROOT)
        except ValueError:
            shown = out
        print(f"报告:{shown}")
    return 0 if not reasons else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default="chroma", choices=("chroma", "milvus"), help="reference store (default chroma)")
    ap.add_argument("--b", default="milvus", choices=("chroma", "milvus"), help="candidate store (default milvus)")
    ap.add_argument("--a-uri", default=None, help="Milvus URI for A (default RAG_MILVUS_URI)")
    ap.add_argument("--b-uri", default=None, help="Milvus URI for B (default RAG_MILVUS_URI)")
    ap.add_argument("--label", default=_dt.date.today().strftime("%Y%m%d"), help="report name suffix")
    ap.add_argument("--out", default=None, help="report path (default docs/bench/replay_gate_<label>.json)")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--min-calls", type=int, default=1000)
    ap.add_argument("--min-agreement", type=float, default=0.99)
    ap.add_argument("--p95-max-ms", type=float, default=None)
    ap.add_argument("--k", type=int, default=K, help="text top-k per call (production dense path uses 60)")
    ap.add_argument("--skip-images", action="store_true")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
