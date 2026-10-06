"""快路 vs 智能体路 —— 复杂问题评测(PLAN.md P2 "评测先行")。

用例:rag/eval/agent_cases.jsonl(34 例:golden_multihop 15 例 + USD 锚点 4 例 +
点名对比 7 例 + 预算配套 5 例 + 跨币种 3 例)。只评**选商品**这一阶段——两条路径
之后都交给同一个流式回答生成阶段,那一步不在这里重复计费/计时。

指标(每例、每次运行):
  route_ok              规则路由是否把它判给智能体(只在 agent 模式计入通过判定)
  relation_correctness  多跳:非锚点商品是否满足与锚点的关系,**一律按人民币比较**
                        (外币按同一份参考汇率换算;path 报 relaxed 的不计入分母)
  constraint_sat        目标品类 / 预算配套总价 / 至少 N 个品类 是否满足
  hit@k                 期望商品是否出现在前 k(对比类:每个点名对象都要覆盖到)
  trajectory_ok         智能体:轮数 ≤ 上限、无未知工具、无参数错误、无被丢弃的 ID
  latency p50/p95       选商品阶段的端到端耗时
  llm_calls / tokens    每请求的 LLM 调用次数与 token(快路这一阶段为 0)
  pass^k                agent 每例跑 k 次,k 次**全部**通过才算该例通过

用法:
  python -m rag.eval.agent_eval --mode fast                 # 只跑快路(需要索引 + 模型)
  python -m rag.eval.agent_eval --mode agent --fake-llm     # 脚本化假 LLM,CI 可跑,无 key
  python -m rag.eval.agent_eval --mode both --k 3           # 真 LLM(读 server/.env 的 key,花钱;给人跑)
  python -m rag.eval.agent_eval --mode both --fake-llm --fx-rate 7.1 --out /tmp/agent_eval.json

注意:--fake-llm 验证的是"编排 + 工具 + 约束 + 回填"整条链路,脚本代替了模型的
决策,因此它的通过率**不能**当成智能体效果写进报告;真实效果只看 live 模式。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("LANGSMITH_TRACING", "false")

CASES_PATH = Path(__file__).parent / "agent_cases.jsonl"
_PRICE_RELATIONS = ("cheaper", "pricier", "same_price")


def load_cases(path: Path = CASES_PATH, only: set[str] | None = None) -> list[dict]:
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [c for c in cases if not only or c["id"] in only]


# --------------------------------------------------------------------------- #
#  判分(纯函数,单测直接覆盖)
# --------------------------------------------------------------------------- #

def _cny(p: dict):
    from app.services.currency import price_in_cny
    return price_in_cny(p)


def relation_ok(relation: str, anchor: dict | None, product: dict) -> bool:
    """单个商品是否满足与锚点的关系(人民币口径;阈值与 multihop.derive_filter 一致)。"""
    if anchor is None:
        return False
    if relation in _PRICE_RELATIONS:
        a, p = _cny(anchor), _cny(product)
        if a is None or p is None:
            return False
        if relation == "cheaper":
            return p < a
        if relation == "pricier":
            return p > a
        return a * 0.8 <= p <= a * 1.2
    if relation == "same_brand":
        from app.services.rag_client import _brand_match_terms
        return bool(_brand_match_terms(anchor.get("brand") or "") & _brand_match_terms(product.get("brand") or ""))
    return True   # pair:关系由目标品类约束体现


def score_run(case: dict, products: list[dict], *, anchor_id: str | None = None,
              relaxed: bool = False, k: int = 5) -> dict:
    """给一次运行打分。products 是该路径最终给出的商品(已按顺序)。"""
    from app.services.rag_client import _catalog_index

    catalog = {p["product_id"]: p for p in _catalog_index() if p.get("product_id")}
    top = products[:k]
    ids = [p.get("product_id") for p in top]
    out: dict = {"ids": ids, "n": len(top), "relaxed": relaxed}

    kind = case["kind"]
    if kind in ("multihop", "usd_anchor"):
        acceptable = case.get("anchor_ids") or []
        used = anchor_id if anchor_id in catalog else None
        anchors = [catalog[used]] if used else [catalog[a] for a in acceptable if a in catalog]
        out["anchor_id"] = used
        out["anchor_ok"] = (used in acceptable) if used else None
        others = [p for p in top if p.get("product_id") not in set(acceptable) | {used}]
        rel_n = rel_ok = 0
        if not relaxed:
            for p in others:
                rel_n += 1
                # 用的锚点已知 → 对它判;未知 → 对所有可接受锚点都得成立(更严)
                rel_ok += all(relation_ok(case["relation"], a, p) for a in anchors) if anchors else 0
        out["relation"] = (rel_ok, rel_n)
        fam = set(case.get("target_family") or [])
        c_n = len(others)
        c_ok = sum(1 for p in others if not fam or p.get("sub_category") in fam)
        out["constraint"] = (c_ok, c_n)
        exp = set(case.get("expect_any") or [])
        out["hit"] = bool(exp & set(ids)) if exp else None
        if case.get("accept_relaxed") and relaxed:
            # 目录里确实没有满足关系的商品:如实走 relaxed 就是正确答案
            out["hit"] = True
        passed = (bool(top) and rel_ok == rel_n and c_ok == c_n and out["hit"] is not False
                  and (not relaxed or bool(case.get("accept_relaxed"))))
    elif kind in ("comparison", "cross_currency"):
        groups = case.get("expect_groups") or []
        covered = [bool(set(g) & set(ids)) for g in groups]
        out["hit"] = all(covered) if groups else None
        out["coverage"] = (sum(covered), len(groups))
        out["relation"] = (0, 0)
        out["constraint"] = (1 if top else 0, 1)
        passed = bool(out["hit"])
    elif kind == "bundle":
        budget = float(case.get("bundle_budget_cny") or 0)
        prices = [_cny(p) for p in top]
        total = sum(x for x in prices if x is not None)
        subs = {p.get("sub_category") for p in top}
        min_subs = int(case.get("bundle_min_subs") or 2)
        need = set(case.get("bundle_need_subs") or [])
        checks = [
            None not in prices and total <= budget,
            len(subs) >= min_subs,
        ]
        out["bundle_total_cny"] = round(total, 2)
        out["constraint"] = (sum(checks), len(checks))
        out["relation"] = (0, 0)
        out["hit"] = bool(need & subs) if need else None
        passed = all(checks) and out["hit"] in (True, None)
    else:
        raise ValueError(f"unknown case kind {kind}")
    out["pass"] = bool(passed)
    return out


def trajectory_ok(trace: dict, max_rounds: int = 3) -> tuple[bool, list[str]]:
    from app.agent.tools import TOOL_SPECS

    problems = []
    if (trace.get("rounds") or 0) > max_rounds:
        problems.append("too_many_rounds")
    for c in trace.get("tool_calls") or []:
        if c.get("name") not in TOOL_SPECS:
            problems.append(f"unknown_tool:{c.get('name')}")
        if c.get("error") in ("invalid_arguments", "invalid_json_arguments", "unknown_tool"):
            problems.append(f"bad_call:{c.get('name')}:{c.get('error')}")
    if trace.get("dropped_ids"):
        problems.append(f"dropped:{len(trace['dropped_ids'])}")
    if trace.get("error"):
        problems.append(f"error:{trace['error']}")
    return (not problems), problems


def percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


# --------------------------------------------------------------------------- #
#  两条路径
# --------------------------------------------------------------------------- #

def _messages(query: str):
    from app.schemas.chat import ChatMessage
    return [ChatMessage(role="user", content=query)]


def run_fast(case: dict) -> dict:
    """复刻 chat.py 检索阶段(纯文本、单轮):多跳 → 单跳 top_k → 预算筛空兜底。"""
    from app.routes.chat import _augment_english, _detect_scene, _reorder_negation_object, _strip_price
    from app.services.constraint_state import build_conversation_filter
    from app.services.contextual_query import build_retrieval_query
    from app.services.currency import normalize_product_prices
    from app.services.rag_client import multi_hop_retrieve, top_k
    from rag.retrieve.multihop import detect_multihop

    q = case["query"]
    t0 = time.perf_counter()
    products, anchor_id, relaxed = [], None, False
    plan = detect_multihop(q)
    if plan is not None:
        try:
            anchor, hop2, trace = multi_hop_retrieve(plan, k=4)
            if anchor and hop2:
                products = normalize_product_prices([anchor] + hop2)
                anchor_id, relaxed = anchor.get("product_id"), bool(trace.get("relaxed"))
        except Exception:
            products = []
    if not products:
        msgs = _messages(q)
        scene = _detect_scene(q)
        rq = _augment_english(build_retrieval_query(msgs), q)
        conv = build_conversation_filter(msgs, None)
        products = normalize_product_prices(top_k(rq, k=5, conversation_filter=conv, intent_text=q,
                                                  relevance_gate=not scene))
        if not products:
            fb = _reorder_negation_object(_strip_price(rq) or rq)
            products = normalize_product_prices(top_k(fb, k=5, intent_text=(_strip_price(q) or None),
                                                      relevance_gate=not scene))
    return {"products": products, "anchor_id": anchor_id, "relaxed": relaxed,
            "latency_ms": (time.perf_counter() - t0) * 1000, "llm_calls": 0, "tokens": 0, "trace": {}}


def run_agent_case(case: dict, *, fake: bool, provider=None) -> dict:
    from app.agent.fake_llm import ScriptedToolLLM
    from app.agent.graph import AgentLimits, run_agent
    from app.agent.router import should_use_agent
    from app.agent.tools import ToolContext, resolve_session_constraints
    from app.services.constraint_state import build_conversation_filter

    q = case["query"]
    route = should_use_agent(q)
    llm = ScriptedToolLLM(case.get("fake_script") or []) if fake else provider
    ctx = ToolContext(session=resolve_session_constraints(build_conversation_filter(_messages(q), None)),
                      bundle_budget_cny=route.bundle_budget_cny or case.get("bundle_budget_cny"))
    limits = AgentLimits.from_env()
    if fake:
        # 假 LLM 不耗时,但首个用例要加载检索模型(冷启动十几秒):放宽总时长只为不误判超时;
        # 真实时延以 live 模式为准。
        limits = AgentLimits(max_tool_rounds=limits.max_tool_rounds, timeout_s=max(limits.timeout_s, 120.0),
                             per_call_timeout_s=limits.per_call_timeout_s, recursion_limit=limits.recursion_limit)
    t0 = time.perf_counter()
    res = asyncio.run(run_agent(q, provider=llm, ctx=ctx, route_reason=route.reason
                                if route.use_agent else (case["kind"] if case["kind"] != "usd_anchor" else "multihop"),
                                limits=limits))
    tr = res.trace
    anchors = tr.get("anchor_ids") or []
    relaxed = any(h.get("relaxed") for h in tr.get("hop_traces") or [])
    return {
        "products": res.products, "anchor_id": anchors[0] if anchors else None, "relaxed": relaxed,
        "latency_ms": (time.perf_counter() - t0) * 1000,
        "llm_calls": tr.get("llm_calls") or 0, "tokens": (tr.get("usage") or {}).get("total_tokens") or 0,
        "trace": tr, "route": route.reason, "routed": route.use_agent, "error": res.error,
    }


# --------------------------------------------------------------------------- #
#  驱动
# --------------------------------------------------------------------------- #

def _fix_fx(rate: float) -> None:
    from app.services import currency
    from app.services.currency import ExchangeRate

    currency.clear_rate_cache()
    currency._request_rate = lambda s, t: ExchangeRate(s, t, rate, "fixed-for-eval")  # type: ignore[assignment]


def evaluate(cases: list[dict], *, mode: str, k_runs: int, fake: bool, provider=None, top_k: int = 5,
             verbose: bool = True) -> dict:
    report: dict = {"mode": mode, "fake_llm": fake, "k": k_runs, "n_cases": len(cases), "paths": {}}
    paths = ["fast", "agent"] if mode == "both" else [mode]
    for path in paths:
        rows, lat, calls, toks = [], [], [], []
        rel_ok = rel_n = con_ok = con_n = hits = hit_n = 0
        traj_ok_n = traj_n = 0
        pass_all = 0
        runs = 1 if path == "fast" else max(1, k_runs)
        for c in cases:
            run_results = []
            for _ in range(runs):
                r = run_fast(c) if path == "fast" else run_agent_case(c, fake=fake, provider=provider)
                s = score_run(c, r["products"], anchor_id=r["anchor_id"], relaxed=r["relaxed"], k=top_k)
                if path == "agent":
                    ok, problems = trajectory_ok(r["trace"])
                    s["trajectory_ok"], s["trajectory_problems"] = ok, problems
                    s["route_ok"] = bool(r.get("routed"))
                    s["pass"] = s["pass"] and ok and s["route_ok"]
                    traj_ok_n += ok
                    traj_n += 1
                lat.append(r["latency_ms"])
                calls.append(r["llm_calls"])
                toks.append(r["tokens"])
                a, b = s["relation"]
                rel_ok, rel_n = rel_ok + a, rel_n + b
                a, b = s["constraint"]
                con_ok, con_n = con_ok + a, con_n + b
                if s.get("hit") is not None:
                    hits += bool(s["hit"])
                    hit_n += 1
                run_results.append(s)
            case_pass = all(s["pass"] for s in run_results)
            pass_all += case_pass
            rows.append({"id": c["id"], "kind": c["kind"], "pass_all_runs": case_pass, "runs": run_results})
            if verbose:
                s0 = run_results[0]
                extra = ""
                if path == "agent":
                    extra = f" traj={'ok' if s0['trajectory_ok'] else ','.join(s0['trajectory_problems'])}"
                print(f"[{path:<5}] {c['id']:<5} {'PASS' if case_pass else 'FAIL'} "
                      f"rel={s0['relation'][0]}/{s0['relation'][1]} con={s0['constraint'][0]}/{s0['constraint'][1]} "
                      f"hit={s0.get('hit')} relaxed={s0['relaxed']} ids={s0['ids']}{extra}", flush=True)
        report["paths"][path] = {
            "cases": rows,
            "pass_k": f"{pass_all}/{len(cases)}",
            "pass_k_rate": round(pass_all / len(cases), 3) if cases else None,
            "relation_correctness": f"{rel_ok}/{rel_n}",
            "constraint_satisfaction": f"{con_ok}/{con_n}",
            "hit_at_k": f"{hits}/{hit_n}",
            "trajectory_ok": f"{traj_ok_n}/{traj_n}" if path == "agent" else None,
            "latency_ms_p50": round(percentile(lat, 0.5) or 0, 1),
            "latency_ms_p95": round(percentile(lat, 0.95) or 0, 1),
            "llm_calls_per_request": round(statistics.mean(calls), 2) if calls else 0,
            "tokens_per_request": round(statistics.mean(toks), 1) if toks else 0,
        }
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["fast", "agent", "both"], default="both")
    ap.add_argument("--fake-llm", action="store_true", help="脚本化假 LLM(无 key、不花钱、CI 可跑)")
    ap.add_argument("--k", type=int, default=1, help="agent 每例运行次数(pass^k)")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--cases", type=Path, default=CASES_PATH)
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--fx-rate", type=float, default=None,
                    help="固定 USD→CNY 汇率(可复现);--fake-llm 时默认 7.0,否则用实时参考汇率")
    ap.add_argument("--out", type=Path, default=None, help="把完整报告写成 JSON")
    args = ap.parse_args(argv)

    fx = args.fx_rate if args.fx_rate is not None else (7.0 if args.fake_llm else None)
    if fx is not None:
        _fix_fx(fx)

    provider = None
    if args.mode in ("agent", "both") and not args.fake_llm:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / "server" / ".env")
        except Exception:
            pass
        from app.services.llm_provider import get_provider
        provider = get_provider()
        if not getattr(provider, "supports_tools", False):
            print(f"provider {getattr(provider, 'name', '?')} 不支持工具调用;用 --fake-llm 或配置 TOKENROUTER_API_KEY")
            return 2

    cases = load_cases(args.cases, {x for x in args.only.split(",") if x} or None)
    report = evaluate(cases, mode=args.mode, k_runs=args.k, fake=args.fake_llm, provider=provider,
                      top_k=args.top_k)
    report["fx_rate"] = fx if fx is not None else "live"
    print("-" * 100)
    for path, summ in report["paths"].items():
        print(f"[{path}] pass^{report['k'] if path == 'agent' else 1} = {summ['pass_k']}  "
              f"relation(CNY) = {summ['relation_correctness']}  constraints = {summ['constraint_satisfaction']}  "
              f"hit@{args.top_k} = {summ['hit_at_k']}"
              + (f"  trajectory = {summ['trajectory_ok']}" if summ.get("trajectory_ok") else "")
              + f"  p50/p95 = {summ['latency_ms_p50']}/{summ['latency_ms_p95']} ms"
              f"  llm_calls/req = {summ['llm_calls_per_request']}  tokens/req = {summ['tokens_per_request']}")
    if args.fake_llm and "agent" in report["paths"]:
        print("注意:--fake-llm 的智能体结果由脚本决定,只证明链路可用,不代表真实模型效果。")
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"report → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
