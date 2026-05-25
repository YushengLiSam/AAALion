# Demo Results — 2026-05-25 (Round 7 / R7.2 backend follow-up)

Re-recorded after Round 7 landed: Sam's eval dashboard merged + Tujie's
synonym/contextual/price-intent work in production + my brand-origin
negation fix (`安热沙` no longer leaks past "不要日系").

Driven on the iPhone 17 Pro simulator via `xcrun simctl launch booted
com.aaalion.lionpick -test-query "..."` after restarting the backend on
the brand-origin-fix SHA (`dc13f32`). Screenshots taken at `simctl io
booted screenshot`.

## Six scenarios (mirrors Sam's eval breakdown)

| # | File | Scenario | Query | Verdict |
|---|---|---|---|---|
| 01 | [`01-basic.png`](01-basic.png) | basic | `推荐一款适合油皮的洗面奶` | ✅ |
| 02 | [`02-filter.png`](02-filter.png) | filter | `200元以下的蓝牙耳机有哪些` | ✅ |
| 03 | [`03-negation.png`](03-negation.png) | negation **(R7 fix)** | `推荐防晒霜，不要日系品牌，不要含酒精` | ✅✅ |
| 04 | [`04-multiturn.md`](04-multiturn.md) | multi-turn | `推荐一款适合油皮的洗面奶` → `再便宜点的呢` | ✅ (SSE-proof only; UI capture deferred to demo video) |
| 05 | [`05-compare.png`](05-compare.png) | compare | `雅诗兰黛小棕瓶和兰蔻小黑瓶哪个更适合熬夜` | ✅ |
| 06 | [`06-no-match.png`](06-no-match.png) | no-match | `推荐一台量子计算机` | ✅ |

## What's new vs Round 6 demos

| Capability | R6 (2026-05-24) | R7 (now) | Owner |
|---|---|---|---|
| 反选准确率 (negation) | "不要日系" leaked 安热沙 | brand-origin lookup drops 安热沙 + 资生堂 etc. — only 法系 / 国货 returned | Shufeng (this round) |
| Multi-turn ("再便宜点的呢") | inherited topic via `contextual_query.py` (Tujie R6.5) | unchanged, still perfect | Tujie |
| Synonym expansion ("无线耳机" → "蓝牙耳机/TWS/降噪耳机") | unchanged from R6.5 | unchanged | Tujie |
| Per-scenario eval dashboard | not in repo | merged on `main` ([`docs/eval_report.html`](../eval_report.html)) | Sam |
| Foreign-price display / totals | source-currency hint and per-currency totals | RMB primary display with dated reference-rate trace; original amount retained | Tujie (R7.2) |

## Headline numbers (R7.2, hybrid+rerank on audited 59-case set)

| Metric | Value | Note |
|---|---|---|
| recall@5 | **0.830** | corrected 19 catalog-mismatched or incomplete golden labels |
| recall@10 | **0.936** | post-audit baseline |
| MRR | **0.778** | CNY-aware price ordering; R7.1 post-audit baseline was 0.771 |
| 反选准确率 | **0.780** | 10 cases carry `forbidden_product_ids`; brand-origin demos verified live |
| no-match correctness | **0.902** | 10 total empty-expected cases |
| mean latency | 4,489 ms | Docker full evaluation run; includes the first-rate lookup path |

The audit changes answer labels, not the retrieval pipeline. These values are
the new baseline and are not presented as a pure algorithm delta from the
pre-audit Round 7 report.

R7.2 adds latest-reference-rate CNY display and CNY-aware price-intent ordering:
recall@5 remains 0.830 while MRR rises from 0.771 to 0.778. The images above
were captured before the FX UI addition, so the conversion behavior is verified
through the API/test run rather than claimed from those screenshots.

See [`docs/EVAL_RESULTS.md`](../EVAL_RESULTS.md) (Sam) and
[`docs/QUALITY_REPORT_2026-05-25.md`](../QUALITY_REPORT_2026-05-25.md)
(Shufeng) for the full breakdown.

## Reproduction

```bash
git fetch && git checkout main
source .venv/bin/activate
python -m rag.eval.report          # regenerates docs/eval_report.html
cd server && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Simulator demos
xcrun simctl boot "iPhone 17 Pro"
xcrun simctl install booted /tmp/lionpick-derived/Build/Products/Debug-iphonesimulator/狮选.app
xcrun simctl launch booted com.aaalion.lionpick -test-query "<query>"
sleep 14
xcrun simctl io booted screenshot docs/demos/2026-05-25/NN-name.png
```

## What still needs touch-driven testing

- Multi-turn UI flow (#04). Captured as text-only via SSE for now;
  visual flow recording is part of the Tier 2 defense demo video
  (QuickTime screencast — see `docs/defense/`).
- iPhone 13 Pro re-test. App from R6 install still works against the
  R7 backend; user should open it and try the 3 hero queries to confirm.
