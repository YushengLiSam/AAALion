# 06 — no-match (anti-hallucination)

**Query**: `推荐一台量子计算机`

**Verdict**: ✅ PASS — system honestly admits no match, pivots to closest neighbors (笔记本电脑 / 平板).

No invented "QuantumLab Pro Z1 ¥99999". The system prompt's "仅基于目录回答" rule + the `no_match_correctness` metric (0.855 in current eval) tracks this.

## What this verifies

The R8 pipeline (with all the new constraint filters + currency norm + stateful state) does NOT degrade the no-match honesty. Tujie's catalog constraint filter only adds hard filters; never lowers no-match correctness.
