"""Run the golden eval and report recall@5 / recall@10 / MRR for
three retriever variants: dense-only, hybrid (dense + BM25),
hybrid+rerank (the production path).

Usage: ``python -m rag.eval.run``
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load() -> list[dict]:
    path = Path(__file__).with_name("golden.jsonl")
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    return cases


def _recall_at_k(retrieved: list[str], expected: set[str], k: int) -> float:
    if not expected:
        return 1.0
    hit = len(expected & set(retrieved[:k]))
    return hit / len(expected)


def _mrr(retrieved: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    for i, pid in enumerate(retrieved, 1):
        if pid in expected:
            return 1.0 / i
    return 0.0


def _retrieve(query_text: str, mode: str, k: int) -> list[str]:
    if mode == "dense":
        from rag.retrieve.query import query as dense_query
        return [h.product_id for h in dense_query(query_text, k=k)]
    if mode == "hybrid":
        from rag.retrieve.hybrid import hybrid_topk
        return [h.product_id for h in hybrid_topk(query_text, k=k)]
    if mode == "hybrid_rerank":
        sys.path.insert(0, str(REPO_ROOT / "server"))
        from app.services.rag_client import top_k as full_top_k
        prods = full_top_k(query_text, k=k)
        return [p["product_id"] for p in prods]
    raise ValueError(f"unknown mode: {mode}")


def main() -> int:
    cases = _load()
    scored = [c for c in cases if c.get("expected_product_ids")]
    print(f"loaded {len(cases)} cases ({len(scored)} with expected ids)\n")

    modes = ["dense", "hybrid", "hybrid_rerank"]
    if os.getenv("RAG_RERANK", "1") == "0":
        modes = ["dense", "hybrid"]

    print(f"{'mode':<18} {'recall@5':<10} {'recall@10':<10} {'MRR':<8}")
    print("-" * 50)

    for mode in modes:
        r5 = r10 = mrr = 0.0
        errors = 0
        for case in scored:
            try:
                retrieved = _retrieve(case["query"], mode, k=10)
            except Exception as e:
                errors += 1
                continue
            expected = set(case["expected_product_ids"])
            r5 += _recall_at_k(retrieved, expected, 5)
            r10 += _recall_at_k(retrieved, expected, 10)
            mrr += _mrr(retrieved, expected)
        n = max(len(scored) - errors, 1)
        suffix = f"  ({errors} errors)" if errors else ""
        print(f"{mode:<18} {r5/n:<10.3f} {r10/n:<10.3f} {mrr/n:<8.3f}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
