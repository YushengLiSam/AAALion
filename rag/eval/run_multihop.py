"""Multi-hop 检索评测。

指标:
  detect_rate           —— 15 例里正确识别为多跳的比例
  relation_accuracy     —— 识别出的关系类型是否正确
  anchor_accuracy       —— hop1 是否锚定到期望商品(标题含 anchor_contains)
  hop2_nonempty         —— hop2 是否返回了结果
  relation_correctness  —— **核心**:hop2 每个结果是否真的满足派生约束
                            (程序化断言,不靠人看,也不靠 LLM)
用法:  python -m rag.eval.run_multihop
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from rag.retrieve.multihop import detect_multihop  # noqa: E402
from app.services.rag_client import multi_hop_retrieve  # noqa: E402


def _price(p: dict):
    v = p.get("price_cny")
    v = v if v is not None else p.get("base_price")
    return float(v) if v is not None else None


def check_relation(relation: str, anchor_price, anchor_brand, results) -> tuple[int, int]:
    """返回 (满足数, 总数)。"""
    ok = 0
    for p in results:
        pr = _price(p)
        good = True
        if relation == "cheaper":
            good = anchor_price is not None and pr is not None and pr < anchor_price
        elif relation == "pricier":
            good = anchor_price is not None and pr is not None and pr > anchor_price
        elif relation == "same_price":
            good = (anchor_price is not None and pr is not None
                    and anchor_price * 0.8 <= pr <= anchor_price * 1.2)
        elif relation == "same_brand":
            b = (p.get("brand") or "").casefold()
            a = (anchor_brand or "").casefold()
            good = bool(a) and (a in b or b in a)
        ok += 1 if good else 0
    return ok, len(results)


def main() -> int:
    cases = [json.loads(l) for l in
             (Path(__file__).parent / "golden_multihop.jsonl").read_text().splitlines() if l.strip()]

    n = len(cases)
    detected = rel_ok = anchor_ok = nonempty = 0
    constraint_ok = constraint_total = 0
    rows = []

    for c in cases:
        plan = detect_multihop(c["query"])
        if plan is None:
            rows.append((c["id"], c["query"], "✗未识别", "", "", ""))
            continue
        detected += 1
        rel_match = plan.relation == c["relation"]
        rel_ok += 1 if rel_match else 0

        anchor, results, trace = multi_hop_retrieve(plan)
        a = trace.get("anchor") or {}
        a_title = a.get("title") or ""
        a_hit = c["anchor_contains"].casefold() in a_title.casefold()
        anchor_ok += 1 if a_hit else 0
        nonempty += 1 if results else 0

        # relaxed 兜底返回的是"最接近的替代"(约束下确实无货),按设计
        # 它们不承诺满足关系,故不计入 relation_correctness 分母;
        # trace.relaxed 会让 prompt 如实说明"没有完全符合的"。
        ok, tot = check_relation(c["relation"], a.get("price_cny"), a.get("brand"), results)
        if not trace.get("relaxed"):
            constraint_ok += ok
            constraint_total += tot

        rows.append((c["id"], c["query"][:26],
                     f"{plan.relation}{'✓' if rel_match else '✗(期望'+c['relation']+')'}",
                     f"{a_title[:18]}{'✓' if a_hit else '✗'}",
                     f"n={len(results)}{' relaxed' if trace.get('relaxed') else ''}",
                     f"{ok}/{tot}"))

    print(f"{'id':<6}{'query':<28}{'关系':<22}{'锚点':<22}{'hop2':<12}{'约束满足'}")
    print("-" * 100)
    for r in rows:
        print(f"{r[0]:<6}{r[1]:<28}{r[2]:<22}{r[3]:<22}{r[4]:<12}{r[5]}")
    print("-" * 100)
    print(f"detect_rate          = {detected}/{n} = {detected/n:.3f}")
    print(f"relation_accuracy    = {rel_ok}/{n} = {rel_ok/n:.3f}")
    print(f"anchor_accuracy      = {anchor_ok}/{n} = {anchor_ok/n:.3f}")
    print(f"hop2_nonempty        = {nonempty}/{n} = {nonempty/n:.3f}")
    if constraint_total:
        print(f"relation_correctness = {constraint_ok}/{constraint_total} = {constraint_ok/constraint_total:.3f}  ← 核心指标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
