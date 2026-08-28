"""RAGAS 生成质量评测 + 自研确定性 fact_grounding。

三层评测体系的第二、三层(第一层是 golden.jsonl 的检索确定性指标):
  · faithfulness       回答的每个论断能否被检索上下文支撑(抗幻觉第三方审计)
  · answer_relevancy   回答是否切题(judge 反推问题 + 本地 bge 比相似度)
  · context_precision  喂给 LLM 的卡片信噪比(补 recall 的盲区)
  · fact_grounding     **自研、零 LLM**:回答里出现的价格是否真在上下文里
                       (或可由上下文价格差值推算 —— 多跳会解释差价)
                       —— 能确定性断言的绝不浪费裁判配额,也补 LLM judge
                       常漏数字错误的短板

刻意**不用** context_recall:它需要参考答案,而我们的 recall@5 已经用人工标注
的商品 id 更硬地测了同一件事。

裁判偏置声明:judge 与被测生成同为 claude-haiku-4-5(自评偏置)。缓解手段:
(1) fact_grounding 提供无偏的确定性对照;(2) --judge-model 可换模型交叉验证。

用法:
  python -m rag.eval.run_ragas --n 5            # 冒烟(先跑这个)
  python -m rag.eval.run_ragas                  # 全量(triples 里所有条)
  python -m rag.eval.run_ragas --metrics fact   # 只跑零成本的确定性指标
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

CACHE_PATH = HERE / ".ragas_judge_cache.json"


# ---------------------------------------------------------------------------
# 自研确定性指标:fact_grounding(零 LLM 调用)
# ---------------------------------------------------------------------------
_PRICE_RE = re.compile(r"¥\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


def fact_grounding(answer: str, contexts: list[str]) -> tuple[int, int, list[str]]:
    """回答里出现的每个价格,是否真的在上下文里出现过。

    返回 (命中数, 总数, 未命中的价格列表)。
    价格是最容易被 LLM 编造、又最容易被用户当真的事实,且能 100% 确定性校验
    —— 正是不该交给 LLM judge 的那类断言。
    """
    ctx = " ".join(contexts)
    ctx_prices = {p.replace(",", "") for p in _PRICE_RE.findall(ctx)}
    # 上下文里的价格同时以 ¥xxx.00 形式存在,把整数形式也纳入
    ctx_norm = set()
    for p in ctx_prices:
        ctx_norm.add(p)
        try:
            f = float(p)
            ctx_norm.add(f"{f:.2f}")
            ctx_norm.add(str(int(f)) if f == int(f) else f"{f}")
        except ValueError:
            pass

    # multi-hop 的回答会解释差价("比它便宜 ¥200"),这是由上下文价格**推算**
    # 出来的合法数字,不是幻觉。把任意两个上下文价格的差值也纳入可接受集合。
    # (这条是实测发现的误报:1099-899=200 被判成编造)
    nums = []
    for p in ctx_prices:
        try:
            nums.append(float(p))
        except ValueError:
            pass
    for i, a1 in enumerate(nums):
        for a2 in nums[i + 1:]:
            d = abs(a1 - a2)
            if d > 0:
                ctx_norm.add(f"{d:.2f}")
                ctx_norm.add(str(int(d)) if d == int(d) else f"{d}")

    ans_prices = [p.replace(",", "") for p in _PRICE_RE.findall(answer)]
    ok, miss = 0, []
    for p in ans_prices:
        cands = {p}
        try:
            f = float(p)
            cands |= {f"{f:.2f}", str(int(f)) if f == int(f) else f"{f}"}
        except ValueError:
            pass
        if cands & ctx_norm:
            ok += 1
        else:
            miss.append(p)
    return ok, len(ans_prices), miss


# ---------------------------------------------------------------------------
# RAGAS judge / embeddings
# ---------------------------------------------------------------------------
def build_judge(model: str):
    """judge 走 TokenRouter(OpenAI 兼容端点)。"""
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    key = os.getenv("TOKENROUTER_API_KEY")
    base = os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")
    if not key:
        raise SystemExit("缺 TOKENROUTER_API_KEY(在 server/.env 里,先 export 或用 --metrics fact)")
    return LangchainLLMWrapper(ChatOpenAI(
        model=model, api_key=key, base_url=base, temperature=0.0, timeout=120))


def build_embeddings():
    """answer_relevancy 的 embedding 用本地 bge —— 中文友好且零成本。"""
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    return LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name="BAAI/bge-small-zh-v1.5"))


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def cache_key(row: dict, metrics: list[str], model: str) -> str:
    payload = json.dumps({"q": row["question"], "a": row["answer"],
                          "c": row["contexts"], "m": sorted(metrics), "jm": model},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--triples", default=str(HERE / "ragas_triples.jsonl"))
    ap.add_argument("--n", type=int, default=0, help="只评前 N 条(0=全部)")
    ap.add_argument("--metrics", default="faith,rel,prec,fact",
                    help="逗号分隔: faith,rel,prec,fact")
    ap.add_argument("--judge-model", default=os.getenv("TOKENROUTER_MODEL", "claude-haiku-4-5"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "eval_ragas.json"))
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.triples).read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("answer") and r.get("contexts")]
    if args.n:
        rows = rows[:args.n]
    want = {m.strip() for m in args.metrics.split(",") if m.strip()}
    print(f"评测 {len(rows)} 条 · 指标 {sorted(want)} · judge={args.judge_model}\n")

    # ---- 确定性指标先跑(零成本)----
    fact_ok = fact_tot = 0
    fact_detail = []
    if "fact" in want:
        for r in rows:
            ok, tot, miss = fact_grounding(r["answer"], r["contexts"])
            fact_ok += ok
            fact_tot += tot
            fact_detail.append({"question": r["question"], "ok": ok, "total": tot, "miss": miss})
        rate = fact_ok / fact_tot if fact_tot else 1.0
        print(f"[确定性] fact_grounding = {fact_ok}/{fact_tot} = {rate:.3f}  (零 LLM 调用)")
        bad = [d for d in fact_detail if d["miss"]]
        for d in bad[:5]:
            print(f"    ⚠ {d['question'][:24]} 编造价格: {d['miss']}")
        print()

    ragas_scores = {}
    per_row = []
    ragas_metrics = [m for m in ("faith", "rel", "prec") if m in want]
    if ragas_metrics:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            faithfulness, answer_relevancy, LLMContextPrecisionWithoutReference,
        )

        # 用**无参考版** context precision:我们没有人工参考答案(那正是引入
        # RAGAS 的理由——免标注)。带参考的版本需要 `reference` 列。
        mmap = {"faith": faithfulness, "rel": answer_relevancy,
                "prec": LLMContextPrecisionWithoutReference()}
        metrics = [mmap[m] for m in ragas_metrics]

        cache = load_cache()
        # 缓存键必须覆盖 question + answer + contexts 全部内容:否则改了
        # contexts 重跑会错误命中旧缓存(实测踩过 —— 修了 contexts 却拿到旧分)。
        ck = cache_key({"question": "|".join(r["question"] for r in rows),
                        "answer": "|".join(r["answer"] for r in rows),
                        "contexts": [c for r in rows for c in r["contexts"]]},
                       ragas_metrics, args.judge_model)
        if ck in cache:
            print("命中 judge 缓存,跳过 LLM 调用 ✓\n")
            ragas_scores = cache[ck]["scores"]
            per_row = cache[ck].get("per_row", [])
        else:
            ds = Dataset.from_list([{
                "question": r["question"],
                "answer": r["answer"],
                "contexts": r["contexts"],
            } for r in rows])
            print(f"调用 judge 评测中(约 {len(rows) * len(metrics) * 2} 次 LLM 调用)...")
            result = evaluate(ds, metrics=metrics,
                              llm=build_judge(args.judge_model),
                              embeddings=build_embeddings(),
                              raise_exceptions=False)
            df = result.to_pandas()
            for m in ragas_metrics:
                col = mmap[m].name
                if col in df.columns:
                    vals = [v for v in df[col].tolist() if v == v]  # 去 NaN
                    ragas_scores[col] = sum(vals) / len(vals) if vals else None
            per_row = df.to_dict("records")
            cache[ck] = {"scores": ragas_scores, "per_row": [
                {k: (v if isinstance(v, (int, float, str, type(None))) else str(v))
                 for k, v in rec.items()} for rec in per_row]}
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False))

        print("[LLM-judge] RAGAS:")
        for k, v in ragas_scores.items():
            print(f"    {k:<22} = {v:.3f}" if v is not None else f"    {k:<22} = n/a")
        print()

    # ---- 自声明 vs 审计 相关性 ----
    if "faith" in want and per_row:
        pairs = []
        for r, rec in zip(rows, per_row):
            cs = r.get("claim_summary") or {}
            tot = (cs.get("verified", 0) + cs.get("inferred", 0))
            if tot:
                f = rec.get("faithfulness")
                if f is not None and f == f:
                    pairs.append((cs["verified"] / tot, float(f)))
        if len(pairs) >= 3:
            n = len(pairs)
            mx = sum(p[0] for p in pairs) / n
            my = sum(p[1] for p in pairs) / n
            cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
            vx = sum((p[0] - mx) ** 2 for p in pairs) ** 0.5
            vy = sum((p[1] - my) ** 2 for p in pairs) ** 0.5
            corr = cov / (vx * vy) if vx and vy else 0.0
            print(f"[亮点] 自声明 vs 审计:[目录✓]占比 与 faithfulness 的相关系数 "
                  f"r = {corr:+.3f}(n={n})")
            print(f"       平均 [目录✓] 占比 {mx:.3f} · 平均 faithfulness {my:.3f}\n")

    report = {
        "n": len(rows),
        "judge_model": args.judge_model,
        "ragas": ragas_scores,
        "fact_grounding": {"ok": fact_ok, "total": fact_tot,
                           "rate": (fact_ok / fact_tot) if fact_tot else None,
                           "violations": [d for d in fact_detail if d["miss"]]},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"✓ 报告写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
