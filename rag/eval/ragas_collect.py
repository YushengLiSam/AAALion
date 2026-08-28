"""RAGAS 采样收集器 —— 从运行中的后端采集 (question, answer, contexts) 三元组。

为什么要单独一步:RAGAS 需要 LLM 的**实际回答**和**实际看到的上下文**,
这些只能从真实链路里拿。这里直接打 /chat/stream,把 SSE 流还原成:
  question  —— 用户问题(多轮取最后一轮)
  answer    —— 拼接所有 delta 事件
  contexts  —— 由 product_card 重建的目录行,格式与 chat.py::_build_catalog
               一致(即 LLM 真正看到的上下文),保证 faithfulness 判得准
另外记录 claim_summary(自声明的 [目录✓]/[推断?] 计数),供 §亮点分析里
"自声明 vs RAGAS 审计"的相关性对照。

用法:
  python -m rag.eval.ragas_collect --n 20            # 默认分层抽样 20 条
  python -m rag.eval.ragas_collect --n 5             # 冒烟
  python -m rag.eval.ragas_collect --base http://127.0.0.1:8000
输出: rag/eval/ragas_triples.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]

DEFAULT_BASE = "http://127.0.0.1:8000"


_SEED_DESC: dict[str, str] | None = None


def _seed_descriptions() -> dict[str, str]:
    """product_id → marketing_description(前120字)。

    为什么需要:chat.py::_build_catalog 给 LLM 的目录行**包含**营销描述
    (材质/科技/克重都在里面),而 SSE 的 product_card 事件为了瘦身不带它。
    如果 contexts 漏掉这段,RAGAS 裁判就看不到回答的证据来源,faithfulness
    会被系统性低估 —— 这是实测踩到的坑(0.29 → 修复后见报告)。
    """
    global _SEED_DESC
    if _SEED_DESC is not None:
        return _SEED_DESC
    import glob
    out: dict[str, str] = {}
    for fp in glob.glob(str(ROOT / "data" / "seed" / "*" / "data" / "*.json")):
        try:
            d = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = d.get("product_id")
        if pid:
            rag = d.get("rag_knowledge") or {}
            out[pid] = (rag.get("marketing_description") or "")[:120]
    _SEED_DESC = out
    return out


def _catalog_line(p: dict) -> str:
    """与 chat.py::_build_catalog **完全同格式** —— LLM 实际看到的那一行,
    包含营销描述。裁判必须看到和生成时一样的上下文,评分才有意义。"""
    price = p.get("price_cny")
    price = price if price is not None else p.get("base_price")
    price_s = f"¥{float(price):.2f}" if price is not None else "价格未知"
    desc = _seed_descriptions().get(p.get("product_id") or "", "")
    return (f"{p.get('product_id')} | {p.get('title')} | {p.get('brand')} | "
            f"{price_s} | {desc}")


def fire(base: str, question: str, timeout: int = 180,
         messages: list[dict] | None = None) -> dict:
    """打一次 /chat/stream,还原 answer + contexts。

    `messages` 给多轮 case 用 —— golden.jsonl 里的多轮样本带完整对话历史,
    只发最后一句会让模型无从回答(实测 answer_relevancy 掉到 0),必须整段发。
    """
    payload = messages if messages else [{"role": "user", "content": question}]
    body = json.dumps({"messages": payload}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/stream", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    deltas: list[str] = []
    contexts: list[str] = []
    product_ids: list[str] = []
    claim = {"verified": 0, "inferred": 0}
    hop_trace = None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            t = ev.get("type")
            if t == "delta":
                deltas.append(ev.get("text", ""))
            elif t == "product_card":
                p = ev.get("product") or {}
                contexts.append(_catalog_line(p))
                if p.get("product_id"):
                    product_ids.append(p["product_id"])
            elif t == "claim_summary":
                claim = {"verified": ev.get("verified", 0),
                         "inferred": ev.get("inferred", 0)}
            elif t == "hop_trace":
                hop_trace = {"relation": ev.get("relation"), "label": ev.get("label")}
    return {
        "answer": "".join(deltas).strip(),
        "contexts": contexts,
        "retrieved_ids": product_ids,
        "claim_summary": claim,
        "hop_trace": hop_trace,
    }


def stratified_sample(cases: list[dict], n: int, seed: int = 42) -> list[dict]:
    """分层抽样:保证否定例、无匹配例都被覆盖(它们最容易暴露幻觉)。"""
    rng = random.Random(seed)
    neg = [c for c in cases if c.get("forbidden")]
    nomatch = [c for c in cases if not c.get("expected_product_ids")]
    normal = [c for c in cases if c not in neg and c not in nomatch]

    want_neg = max(1, round(n * 0.25))
    want_nom = max(1, round(n * 0.15))
    want_nrm = max(0, n - want_neg - want_nom)

    out = (rng.sample(neg, min(want_neg, len(neg)))
           + rng.sample(nomatch, min(want_nom, len(nomatch)))
           + rng.sample(normal, min(want_nrm, len(normal))))
    return out[:n]


# 多跳样本(现有 golden 里没有,单独补进来,顺带审计多跳回答的忠实度)
MULTIHOP_EXTRA = [
    {"query": "比HOKA那双跑鞋便宜的跑鞋", "tags": ["multihop"]},
    {"query": "跟特步跑鞋同价位的其他跑鞋", "tags": ["multihop"]},
    {"query": "和iPhone一样牌子的平板", "tags": ["multihop"]},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="从 golden.jsonl 抽多少条")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--multihop", type=int, default=3, help="额外多跳样本数")
    ap.add_argument("--out", default=str(HERE / "ragas_triples.jsonl"))
    args = ap.parse_args()

    cases = [json.loads(l) for l in (HERE / "golden.jsonl").read_text().splitlines() if l.strip()]
    picked = stratified_sample(cases, args.n)
    picked += MULTIHOP_EXTRA[:args.multihop]

    print(f"采样 {len(picked)} 条(golden {args.n} + multihop {min(args.multihop, len(MULTIHOP_EXTRA))})")
    print(f"后端: {args.base}\n")

    rows = []
    for i, c in enumerate(picked, 1):
        q = c["query"]
        try:
            got = fire(args.base, q, messages=c.get("messages"))
        except Exception as e:
            print(f"  [{i:>2}/{len(picked)}] ✗ {q[:28]} — {type(e).__name__}")
            continue
        row = {
            "question": q,
            "answer": got["answer"],
            "contexts": got["contexts"],
            "retrieved_ids": got["retrieved_ids"],
            "expected_product_ids": c.get("expected_product_ids", []),
            "forbidden": c.get("forbidden", []),
            "tags": c.get("tags", []),
            "is_multiturn": bool(c.get("messages")),
            "claim_summary": got["claim_summary"],
            "hop_trace": got["hop_trace"],
        }
        rows.append(row)
        cs = got["claim_summary"]
        print(f"  [{i:>2}/{len(picked)}] ✓ {q[:26]:<28} ctx={len(got['contexts'])} "
              f"ans={len(got['answer'])}字 [目录✓]{cs['verified']}/[推断?]{cs['inferred']}")

    out = Path(args.out)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(f"\n✓ 写入 {out}({len(rows)} 条)")
    empty_ctx = sum(1 for r in rows if not r["contexts"])
    empty_ans = sum(1 for r in rows if not r["answer"])
    print(f"  空上下文 {empty_ctx} 条 · 空回答 {empty_ans} 条(空的会被评测跳过)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
