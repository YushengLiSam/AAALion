# Demo Results — 2026-05-25 evening (Round 8)

Re-recorded after R8 integration: Sam's eval dashboard + cache endpoint, Tujie's currency normalization + catalog constraints + stateful filters, plus my R8 improvements (B1 cache panel in iOS Settings, B2 multi-turn negation persistence).

Driven on iPhone 17 Pro simulator via `xcrun simctl launch booted -test-query`. Backend at `2f9b6c4 + 672c6fc (shufeng)`. Eval baseline (71 cases): recall@5 = 0.983, MRR = 0.844, negation_accuracy = 1.000, median latency 68ms.

## Scenarios

| # | File | Scenario | What it proves | Verdict |
|---|---|---|---|---|
| 01 | [`01-basic.png`](01-basic.png) | basic recommendation | sanity | ✅ |
| 02 | [`02-filter.png`](02-filter.png) | price filter `200元以下` | Tujie's `price_intent` + budget filter | ✅ |
| 03 | [`03-negation.png`](03-negation.png) | negation `不要日系不要含酒精` | brand-origin filter (R7 fix) | ✅ |
| 04 | [`04-multiturn.md`](04-multiturn.md) | multi-turn `再便宜点的呢` | Tujie's contextual + stateful | ✅ (SSE log) |
| 05 | [`05-compare.png`](05-compare.png) | A-vs-B comparison | structured comparison | ✅ |
| 06 | [`06-no-match.png`](06-no-match.png) | no-match `量子计算机` | anti-hallucination | ✅ |
| 07 | [`07-currency.png`](07-currency.png) | currency norm `Sony WH-1000XM5 多少钱` | Tujie's R7.2 FX normalization | ✅ |
| 08 | [`08-stateful-step1.png`](08-stateful-step1.png) + [`08-stateful.md`](08-stateful.md) | stateful constraint inheritance | Tujie's R7.5 constraint state | ✅ (SSE log) |
| 09 | [`09-multiturn-negation.md`](09-multiturn-negation.md) | **multi-turn negation (R8 B2 fix)** — "再便宜点的呢" still excludes JP | **R8 B2 bug closed**: keyword exclusion now persists across turns | ✅✅ |

## What's new vs Round 7

| Capability | R7 (5-25 PM) | **R8 (5-25 evening)** | Owner |
|---|---|---|---|
| Negation accuracy (single-turn) | 0.733 | **1.000** | Sam (data audit + local fallback) |
| Negation persistence across turns | broken (turn 2 lost "不要日系") | **fixed via Filter.exclude_keywords** | Shufeng (R8 B2) |
| Catalog constraint filters during retrieval | none | category / brand / RMB budget hard-filtered pre-rank | Tujie (R7.4) |
| Foreign price CNY display | mixed `¥`+`$` per currency | **normalized to ¥ live FX (Frankfurter)** | Tujie (R7.2) |
| Stateful multi-turn constraints | partial (anchor only) | full filter inherit + 不限/品牌不限/品类不限 cancels | Tujie (R7.5) |
| Cache hit-rate observability | endpoint shipped, no UI | **iOS Settings panel with auto-poll** | Sam (endpoint) + Shufeng (R8 B1 iOS panel) |
| Docker prewarm + `/ready` 503 guard | none | model warmup at lifespan startup | Tujie (R7.6) |

## Headline numbers

```
hybrid_rerank, 71-case golden:
  recall@5            0.983
  recall@10           0.995
  MRR                 0.844
  negation_accuracy   1.000
  median latency      68 ms (cache-warm path; fast-path widely triggered)

  multiturn tag         recall@5 1.000  neg-acc 1.000
  negation tag          recall@5 0.949  neg-acc 1.000
  brand-origin tag      recall@5 1.000  neg-acc 1.000
  constraint-state tag  recall@5 1.000  neg-acc 1.000
```

## What still needs touch-driven testing

- Cache panel (R8 B1) on simulator's Settings sheet — captured via `/cache/stats` curl (12 reqs, 2 hits, 16.7% hit-rate). User verifies the panel renders correctly on iPhone in Sub-Plan D.
- Multi-turn UI flow (#04, #08, #09) — `simctl -test-query` is single-shot. Captured via SSE log in sidecars; full UI flow goes into the deferred defense demo video.

## Reproduction

```bash
git pull origin main
git checkout shufeng                # at 672c6fc (R8 improvements)
cd server && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# (wait for /ready 200, ~6s on dev Mac)

xcrun simctl boot "iPhone 17 Pro"
cd ../client/AAALionApp && xcodegen generate && xcodebuild build ...
xcrun simctl install booted .../狮选.app
xcrun simctl launch booted com.aaalion.lionpick -test-query "<query>"
sleep 14
xcrun simctl io booted screenshot docs/demos/2026-05-25-evening/NN-name.png
```
