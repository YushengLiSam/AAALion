"""检索评测门禁(CI 用):逐 case 对比基线,召回回退就让 job 失败。

三个子命令,本地生成基线和 CI 跑的是**同一套命令、同一套设置**::

    python -m rag.eval.gate build-index                 # 只用 CPU,从 data/seed 重建文本索引
    python -m rag.eval.gate run --out /tmp/cur.json     # 只跑检索(生产路径 hybrid_rerank)
    python -m rag.eval.gate compare --baseline docs/bench/eval_baseline.json --current /tmp/cur.json

判定规则(compare):
  * 基线里是"命中"(recall@5 > 0)的 case 变成"未命中" → 失败,并逐条列出;
  * 基线里 top-5 不含 forbidden 商品的 case 现在漏出 forbidden → 失败(反选回退);
  * 任一评测集的平均 recall@5 或 MRR 比基线低超过 0.02 → 失败;
  * 有 case 抛异常 → 失败;
  * 基线和本次的检索设置(rerank 参数 / 汇率表 / 模式)不一致 → 退出码 2,拒绝比较
    (不同设置下的数字没有可比性,得先重新生成基线)。
新增 / 删除的 case 只提示不判失败。

**绝不调用 LLM**(对应线上的两个 LLM 点:``rag.retrieve.negation.extract_negation`` 和
``rag.retrieve.rewrite``,两者都只在 ``TOKENROUTER_API_KEY`` 非空时才发请求):
  * 进程一开始就把所有 LLM key 置成空串、``LLM_PROVIDER=echo``。置空串而不是删除,是因为
    ``app.config`` 会 ``load_dotenv(server/.env)``,而 dotenv 默认不覆盖已存在的变量——
    本机有 server/.env 也不会把 key 读回来;
  * 再给 ``urllib.request.urlopen`` / ``httpx`` 套一层守卫:目标是 LLM 网关或
    ``/chat/completions`` 一律直接抛错并计数,run 结束时计数非零就整体失败。

为了让基线可复现,run 还固定了三件和"检索质量"无关、但会让结果漂移的东西:
  * 设备:强制 CPU(本机 Mac 默认会用 MPS,CI 和线上 VM 都是 CPU);
  * 汇率:不访问 Frankfurter,用固定汇率表(``FIXED_FX``),否则每天汇率一变,
    人民币预算过滤的边界商品就可能进出 top-5;
  * 重排参数:默认取线上 server/.env 的值 RERANK_INPUT_CAP=10、RERANK_MAX_LENGTH=128
    (CI workflow 里也显式写了一遍),实际生效值写进结果 meta。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

GATE_VERSION = 1

#: 线上 server/.env 里的重排参数(PLAN.md §9 2026-10-07 记录;本机默认是 0 / 256)。
PRODUCTION_RETRIEVAL_ENV: dict[str, str] = {
    "RERANK_INPUT_CAP": "10",
    "RERANK_MAX_LENGTH": "128",
    "RAG_HARD_FILTERS": "1",
    "RAG_RERANK": "1",
    "RAG_PREWARM": "1",
}

#: 固定汇率(1 单位外币 = rate 人民币)。目录里只有 USD 外币商品(20 件)。
FIXED_FX: dict[str, float] = {"USD": 7.10}

#: 参与门禁的评测集:名字 → golden 文件。只跑生产路径 hybrid_rerank。
GOLDEN_SETS: dict[str, Path] = {
    "golden": REPO_ROOT / "rag" / "eval" / "golden.jsonl",
    "golden_compositional": REPO_ROOT / "rag" / "eval" / "golden_compositional.jsonl",
}
GATE_MODE = "hybrid_rerank"

MEAN_DROP_TOLERANCE = 0.02

_LLM_KEY_VARS = ("TOKENROUTER_API_KEY", "ANTHROPIC_API_KEY", "DOUBAO_API_KEY", "OPENAI_API_KEY")
_LLM_URL_MARKERS = (
    "tokenrouter",
    "anthropic.com",
    "volces.com",
    "api.openai.com",
    "/chat/completions",
    "/v1/messages",
)

_blocked_llm_calls: list[str] = []


# ----------------------------------------------------------------------------
# 运行环境:禁 LLM、强制 CPU、固定汇率、线上重排参数
# ----------------------------------------------------------------------------

def disable_llm() -> None:
    """把所有 LLM key 置空串并装上网络守卫。必须在 import app.* / rag.retrieve.* 之前调用。"""
    for var in _LLM_KEY_VARS:
        os.environ[var] = ""
    os.environ["LLM_PROVIDER"] = "echo"
    os.environ["AGENT_PATH"] = "off"

    import urllib.request

    real_urlopen = urllib.request.urlopen

    def _guarded_urlopen(url, *args, **kwargs):
        target = url if isinstance(url, str) else getattr(url, "full_url", str(url))
        _check_url(str(target))
        return real_urlopen(url, *args, **kwargs)

    urllib.request.urlopen = _guarded_urlopen  # type: ignore[assignment]

    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx 在 server/requirements.txt 里
        return
    real_send = httpx.Client.send
    real_asend = httpx.AsyncClient.send

    def _guarded_send(self, request, *args, **kwargs):
        _check_url(str(request.url))
        return real_send(self, request, *args, **kwargs)

    async def _guarded_asend(self, request, *args, **kwargs):
        _check_url(str(request.url))
        return await real_asend(self, request, *args, **kwargs)

    httpx.Client.send = _guarded_send  # type: ignore[assignment]
    httpx.AsyncClient.send = _guarded_asend  # type: ignore[assignment]


def _check_url(url: str) -> None:
    low = url.lower()
    if any(marker in low for marker in _LLM_URL_MARKERS):
        _blocked_llm_calls.append(url)
        raise RuntimeError(f"rag.eval.gate: LLM call blocked ({url})")


def force_cpu() -> None:
    """强制 CPU:隐藏 CUDA,并让 torch 认为没有 MPS(sentence-transformers 据此选设备)。"""
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        import torch
    except ImportError:  # pragma: no cover
        return
    try:
        torch.backends.mps.is_available = lambda: False  # type: ignore[assignment]
    except AttributeError:  # pragma: no cover - 老版本 torch 没有 mps
        pass


def apply_production_retrieval_env() -> dict[str, str]:
    """未显式设置的重排参数取线上值;返回实际生效值(写进 meta)。"""
    effective = {}
    for key, value in PRODUCTION_RETRIEVAL_ENV.items():
        os.environ.setdefault(key, value)
        effective[key] = os.environ[key]
    return effective


def pin_fx() -> None:
    """用 FIXED_FX 替换实时汇率请求(只替换网络那一步,换算逻辑照旧走生产代码)。"""
    from app.services import currency

    def _fixed_rate(source: str, target: str):
        if target != currency.TARGET_CURRENCY or source not in FIXED_FX:
            raise ValueError(f"no fixed FX rate for {source}/{target}")
        return currency.ExchangeRate(
            source_currency=source,
            target_currency=target,
            rate=FIXED_FX[source],
            rate_date="fixed-for-gate",
            provider="rag.eval.gate FIXED_FX",
        )

    currency._request_rate = _fixed_rate  # type: ignore[assignment]
    currency.clear_rate_cache()


def _versions() -> dict[str, str]:
    out = {"python": platform.python_version(), "platform": f"{platform.system()}-{platform.machine()}"}
    for mod in ("torch", "transformers", "sentence_transformers", "chromadb"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            out[mod] = "n/a"
    return out


# ----------------------------------------------------------------------------
# build-index / run
# ----------------------------------------------------------------------------

def cmd_build_index(_args: argparse.Namespace) -> int:
    disable_llm()
    force_cpu()
    from rag.ingest.run import main as ingest_main

    return ingest_main(["--rebuild"])


def case_key(case: dict, seen: dict[str, int]) -> str:
    """稳定的 case 标识:有 id 用 id,否则对 query / messages 取 sha1 前 12 位;重复的加 #n。"""
    if case.get("id"):
        base = str(case["id"])
    else:
        payload = json.dumps(
            {"query": case.get("query"), "messages": case.get("messages")},
            ensure_ascii=False,
            sort_keys=True,
        )
        base = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}#{n}"


def summarize(result: dict, golden_path: Path) -> dict:
    """把 core.evaluate() 的结果压成门禁需要的逐 case 记录。"""
    from rag.eval.core import load_cases

    cases = load_cases(golden_path)
    block = result["modes"][GATE_MODE]
    per_case = block["per_case"]
    if len(per_case) != len(cases):  # pragma: no cover - core.evaluate 一一对应
        raise RuntimeError("per_case/cases length mismatch")
    seen: dict[str, int] = {}
    rows = []
    for case, rec in zip(cases, per_case):
        metrics = rec.get("metrics") or {}
        expected = rec.get("expected") or []
        forbidden = rec.get("forbidden") or []
        retrieved = rec.get("retrieved") or []
        row: dict[str, Any] = {
            "key": case_key(case, seen),
            "query": rec.get("query"),
            "expected": expected,
            "retrieved_top10": retrieved[:10],
            "error": rec.get("error"),
        }
        if expected:
            r5 = metrics.get("recall@5")
            row["recall@5"] = r5
            row["mrr"] = metrics.get("mrr")
            row["hit"] = bool(r5 and r5 > 0)
        if forbidden:
            row["forbidden"] = forbidden
            row["negation_clean"] = not (set(forbidden) & set(retrieved[:5]))
        rows.append(row)
    overall = block["overall"]
    return {
        "golden_file": str(golden_path.relative_to(REPO_ROOT)),
        "n_cases": len(rows),
        "n_positive": sum(1 for r in rows if "hit" in r),
        "errors": block.get("errors", 0),
        "overall": {
            "recall@5": overall.get("recall@5"),
            "mrr": overall.get("mrr"),
            "negation_accuracy": overall.get("negation_accuracy"),
            "hits": sum(1 for r in rows if r.get("hit")),
        },
        "cases": rows,
    }


def cmd_run(args: argparse.Namespace) -> int:
    disable_llm()
    force_cpu()
    settings = apply_production_retrieval_env()
    pin_fx()

    from rag.eval.core import evaluate

    out: dict[str, Any] = {
        "gate_version": GATE_VERSION,
        "meta": {
            "mode": GATE_MODE,
            "retrieval_env": settings,
            "fixed_fx_to_cny": FIXED_FX,
            "rag_store": os.getenv("RAG_STORE") or "chroma",
            "device": "cpu",
            "llm": "disabled (keys blanked, network guard)",
            "versions": _versions(),
        },
        "sets": {},
    }
    for name, path in GOLDEN_SETS.items():
        if not path.exists():
            print(f"[gate] skip {name}: {path} not found", file=sys.stderr)
            continue
        print(f"[gate] running {name} ({GATE_MODE}) …", file=sys.stderr)
        result = evaluate(modes=[GATE_MODE], k=10, golden_path=path)
        out["sets"][name] = summarize(result, path)
        ov = out["sets"][name]["overall"]
        print(
            f"[gate] {name}: recall@5={_f(ov['recall@5'])} mrr={_f(ov['mrr'])} "
            f"hits={ov['hits']}/{out['sets'][name]['n_positive']} errors={out['sets'][name]['errors']}",
            file=sys.stderr,
        )

    out["meta"]["blocked_llm_calls"] = len(_blocked_llm_calls)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[gate] wrote {args.out}", file=sys.stderr)
    if _blocked_llm_calls:
        print(f"[gate] FAIL: {len(_blocked_llm_calls)} LLM call(s) were attempted and blocked", file=sys.stderr)
        return 1
    return 0


# ----------------------------------------------------------------------------
# compare
# ----------------------------------------------------------------------------

def _f(v: Any) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "-"


def _settings_fingerprint(doc: dict) -> dict:
    meta = doc.get("meta") or {}
    return {
        "gate_version": doc.get("gate_version"),
        "mode": meta.get("mode"),
        "retrieval_env": meta.get("retrieval_env"),
        "fixed_fx_to_cny": meta.get("fixed_fx_to_cny"),
        "rag_store": meta.get("rag_store"),
    }


def compare(baseline: dict, current: dict, tolerance: float = MEAN_DROP_TOLERANCE) -> dict:
    """纯函数:返回 {"ok", "settings_mismatch", "failures", "notes", "rows"}。"""
    report: dict[str, Any] = {"ok": True, "settings_mismatch": None, "failures": [], "notes": [], "rows": []}
    fb, fc = _settings_fingerprint(baseline), _settings_fingerprint(current)
    if fb != fc:
        report["ok"] = False
        report["settings_mismatch"] = {"baseline": fb, "current": fc}
        return report
    if (current.get("meta") or {}).get("blocked_llm_calls"):
        report["ok"] = False
        report["failures"].append("current run attempted LLM calls (blocked)")

    for name, bset in (baseline.get("sets") or {}).items():
        cset = (current.get("sets") or {}).get(name)
        if cset is None:
            report["ok"] = False
            report["failures"].append(f"[{name}] missing from current run")
            continue
        bov, cov = bset.get("overall") or {}, cset.get("overall") or {}
        for metric in ("recall@5", "mrr"):
            b, c = bov.get(metric), cov.get(metric)
            delta = (c - b) if isinstance(b, (int, float)) and isinstance(c, (int, float)) else None
            report["rows"].append({"set": name, "metric": metric, "baseline": b, "current": c, "delta": delta})
            if delta is None:
                report["ok"] = False
                report["failures"].append(f"[{name}] {metric} unavailable (baseline={b}, current={c})")
            elif -delta > tolerance + 1e-9:
                report["ok"] = False
                report["failures"].append(
                    f"[{name}] mean {metric} dropped {-delta:.3f} > {tolerance:.2f} ({_f(b)} → {_f(c)})"
                )
        if cset.get("errors"):
            report["ok"] = False
            report["failures"].append(f"[{name}] {cset['errors']} case(s) raised errors")

        bcases = {r["key"]: r for r in bset.get("cases") or []}
        ccases = {r["key"]: r for r in cset.get("cases") or []}
        for key, b in bcases.items():
            c = ccases.get(key)
            if c is None:
                report["notes"].append(f"[{name}] case removed: {b.get('query')!r}")
                continue
            if b.get("hit") and not c.get("hit"):
                report["ok"] = False
                report["failures"].append(
                    f"[{name}] hit → miss: {c.get('query')!r} expected={c.get('expected')} "
                    f"got top5={(c.get('retrieved_top10') or [])[:5]}"
                )
            elif "hit" in b and not b.get("hit") and c.get("hit"):
                report["notes"].append(f"[{name}] miss → hit: {c.get('query')!r}")
            if b.get("negation_clean") and c.get("negation_clean") is False:
                report["ok"] = False
                leaked = sorted(set(c.get("forbidden") or []) & set((c.get("retrieved_top10") or [])[:5]))
                report["failures"].append(f"[{name}] negation leak: {c.get('query')!r} forbidden in top5={leaked}")
        for key, c in ccases.items():
            if key not in bcases:
                report["notes"].append(f"[{name}] new case (not gated until baseline refresh): {c.get('query')!r}")
    return report


def render_markdown(report: dict) -> str:
    lines = ["## Retrieval eval gate", ""]
    if report.get("settings_mismatch"):
        mm = report["settings_mismatch"]
        lines += [
            "**SETTINGS MISMATCH** — baseline and current run used different retrieval settings; "
            "regenerate the baseline (see docs/RUNBOOK_OPS.md).",
            "",
            f"- baseline: `{json.dumps(mm['baseline'], ensure_ascii=False)}`",
            f"- current:  `{json.dumps(mm['current'], ensure_ascii=False)}`",
        ]
        return "\n".join(lines) + "\n"
    lines.append(f"**{'PASS' if report['ok'] else 'FAIL'}**")
    lines += ["", "| set | metric | baseline | current | Δ |", "|---|---|---|---|---|"]
    for r in report["rows"]:
        d = r["delta"]
        lines.append(
            f"| {r['set']} | {r['metric']} | {_f(r['baseline'])} | {_f(r['current'])} | "
            f"{(f'{d:+.3f}' if isinstance(d, (int, float)) else '-')} |"
        )
    if report["failures"]:
        lines += ["", "### Failures", ""] + [f"- {x}" for x in report["failures"]]
    if report["notes"]:
        lines += ["", "### Notes", ""] + [f"- {x}" for x in report["notes"]]
    return "\n".join(lines) + "\n"


def cmd_compare(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    report = compare(baseline, current, tolerance=args.tolerance)
    md = render_markdown(report)
    print(md)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(md)
        except OSError:
            pass
    if report["settings_mismatch"]:
        return 2
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rag.eval.gate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-index", help="CPU-only rebuild of the text index from data/seed")
    p_run = sub.add_parser("run", help="retrieval-only eval → JSON (no LLM)")
    p_run.add_argument("--out", required=True)
    p_cmp = sub.add_parser("compare", help="per-case comparison against a baseline")
    p_cmp.add_argument("--baseline", required=True)
    p_cmp.add_argument("--current", required=True)
    p_cmp.add_argument("--tolerance", type=float, default=MEAN_DROP_TOLERANCE)
    args = ap.parse_args(argv)
    return {"build-index": cmd_build_index, "run": cmd_run, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
