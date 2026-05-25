"""Run the golden eval and print recall@5 / recall@10 / MRR for
three retriever variants: dense-only, hybrid (dense + BM25),
hybrid+rerank (the production path).

Now delegates the heavy lifting to ``rag.eval.core.evaluate()`` so the
HTML dashboard (``rag.eval.report``) can reuse the same logic.

Usage: ``python -m rag.eval.run``
Env:   ``RAG_RERANK=0`` skips the hybrid_rerank mode for a quick sanity run.
"""

from __future__ import annotations

import os

from rag.eval.core import MODES, evaluate, load_cases


def _fmt(v: float | None) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "  -  "


def main() -> int:
    modes = list(MODES)
    if os.getenv("RAG_RERANK", "1") == "0":
        modes = [m for m in modes if m != "hybrid_rerank"]

    result = evaluate(modes=modes, k=10)
    meta = result["meta"]
    cases = load_cases()
    multi_turn = sum(1 for c in cases if c.get("messages"))
    no_match = sum(1 for c in cases if c.get("expected_product_ids") == [])
    n_with_expected = sum(1 for c in cases if c.get("expected_product_ids"))

    print(
        f"loaded {meta['n_cases']} cases "
        f"({n_with_expected} with expected ids, "
        f"{multi_turn} multi-turn, "
        f"{no_match} no-match-expected)"
    )
    print(f"  dataset: {meta['dataset_size']} products  ·  k={meta['k']}\n")

    # All metrics, in a stable column order. Negation / no-match are
    # case-class-specific so they show meaningful averages only across
    # the cases that have them; aggregator already handles that.
    metric_cols = (
        "recall@5",
        "recall@10",
        "mrr",
        "precision@5",
        "negation_accuracy",
        "no_match_correctness",
        "latency_ms",
    )
    head = f"{'mode':<18}" + "".join(f"{c:<22}" for c in metric_cols)
    print(head)
    print("-" * len(head))

    for mode in modes:
        block = result["modes"][mode]
        overall = block["overall"]
        line = f"{mode:<18}"
        for col in metric_cols:
            v = overall.get(col)
            if col == "latency_ms" and isinstance(v, (int, float)):
                line += f"{v:>10.1f}ms        "
            else:
                line += f"{_fmt(v):<22}"
        err_n = block.get("errors", 0)
        if err_n:
            line += f"  ({err_n} errors)"
        print(line)

    print()
    print("→ For a rich HTML dashboard with per-tag breakdown, run:")
    print("    python -m rag.eval.report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
