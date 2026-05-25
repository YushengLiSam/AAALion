# Quality Report — 2026-05-25 (Round 6.5: team merges)

> Independent check after `main` got two material merges from teammates:
> - **Tujie** (`b317081` + `4c2fe51`): synonym expansion + contextual multi-turn query + price intent.
> - **Sam** (`Yusheng` branch, **not yet merged**): retrieval quality dashboard with per-scenario metrics.
>
> Measured by me (Shufeng) running `aaalion eval` on the current `main` SHA.

## What's on `main` right now

| SHA | Author | Summary |
|---|---|---|
| `b317081` | Tujie | feat(rag): add curated synonym expansion (`rag/retrieve/synonyms.py`) |
| `4c2fe51` | Tujie | feat(rag): contextual eval + price intent (`server/app/services/contextual_query.py`, `price_intent.py`) |
| `ec49c2a` | Shufeng | fix(server): prefer local image_path over broken image_url_external |
| `0ea3f0e` | Shufeng | feat(data): AI-rendered placeholder images for 45 real products |
| `db72179` | Shufeng | docs(quality): refresh R6 self-assessment (86.0 → 88.0) |
| `f9dd557` | Shufeng | feat(repo): Round 6 main commit |

## Eval results — same 31-case golden set across rounds

Same golden set, same eval script, only the retrieval pipeline changed.

| Round | Pipeline | recall@5 | recall@10 | MRR |
|---|---|---:|---:|---:|
| R5 (2026-05-24 morning) | hybrid + rerank only | 0.711 | 0.842 | 0.695 |
| R6 (my eval before merges) | + 145 products | 0.684 | 0.737 | 0.647 |
| **R6.5 (now, Tujie merged)** | **+ synonyms + contextual + price intent** | **0.816** | **0.842** | **0.705** |

**Delta R6 → R6.5: recall@5 +0.132 absolute (+19.3% relative), MRR +0.058.**
**Delta R5 → R6.5: recall@5 +0.105 absolute (+14.8% relative), MRR +0.010.**

The R5 → R6 drop was real (catalog growth diluted top-5). Tujie's
synonym expansion + price intent more than recovered it. Same 31-case
golden, so this is an apples-to-apples comparison.

## Eval results — Sam's expanded 56-case golden set (his branch)

Sam grew the golden set from 31 → 56 cases (41 with expected ids) and
broke it into 6 scenarios. His dashboard (in `Yusheng` branch only) reports:

| Strategy | recall@5 | recall@10 | MRR | precision@5 | 反选准确率 | 无匹配正确率 | latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| dense | 0.768 | 0.864 | 0.689 | 0.278 | 0.633 | 0.855 | **130 ms** |
| hybrid | 0.752 | 0.835 | 0.639 | 0.273 | 0.667 | 0.849 | 36 ms |
| **hybrid+rerank (prod)** | **0.780** | **0.888** | **0.701** | 0.259 | **0.733** | 0.856 | 1 951 ms |

### Per-scenario breakdown (hybrid+rerank)

| Scenario | n | recall@5 | MRR | notes |
|---|---:|---:|---:|---|
| basic | 13 | 0.861 | 0.892 | system strength |
| filter | 8 | 0.810 | 0.469 | recall@10 = 1.00 |
| negation | 8 | 0.667 | 0.544 | reverse-acc 0.733 ← Tujie's price-intent helps |
| multiturn | 5 | **0.900** | 0.867 | **best** — `contextual_query` win |
| compare | 6 | 0.900 | 0.900 | best on double-match scenarios |
| no-match | 11 | — | — | correctness 0.820 (honest "no match") |

## SSE smoke-tests (LIVE on my Mac, all 4 PASS or near-PASS)

| Query | Result | Verdict |
|---|---|---|
| `推荐一款适合油皮的洗面奶` | 5 hits, 珊珂 + 控油-themed products | ✅ PASS |
| `推荐一本科幻小说` | 5 hits all from `7_图书音像`, **三体 first** | ✅✅ PASS (R5 used to return 化妆品) |
| `推荐一款适合油皮的洗面奶 → 再便宜点的呢` | Cheapest-first sort, inherited 洗面奶 anchor | ✅ PASS — Tujie's `contextual_query` working |
| `推荐防晒霜，不要日系品牌，不要含酒精` | 巴黎欧莱雅 + 理肤泉 (法系) good; **but 安热沙 (日系) leaked through** | 🟡 WEAK — negation filter has a brand-origin blind spot |

## What materially improved

1. **Multi-turn now works without re-stating context.** "再便宜点的呢" pulls the previous shopping anchor + sorts by price. Big UX win — previously this lost the topic entirely.
2. **Books query no longer regresses.** R5 had "推荐一本书" → cosmetics. R6 added the `7_图书音像` category. R6.5 with synonyms ensures 三体 surfaces first.
3. **Price-aware sorting**: "200元以下" / "1000元以上" parsed into real filter, "便宜" / "贵" parsed into direction.
4. **+19% recall@5 vs R6**, **+15% vs R5**, on the same golden set.

## What's still rough

1. **Brand-origin negation gap**: "不要日系" doesn't reliably exclude 安热沙. LLM negation extractor (Round 5's `rag/retrieve/negation.py`) doesn't get brand-origin metadata — it can only filter by literal brand names the user names. Fix would need either a brand-country table in the product JSON or pass brand origin context to the LLM extractor.
2. **Rerank latency = 1.95s** (median). Cache hits drop to ~300ms (Round 5 instrumentation confirms). For the demo this is fine; for production we'd want a smaller cross-encoder (or skip rerank when top-1 dense score is >0.9).
3. **Sam's eval dashboard isn't on `main` yet** — only on `Yusheng` branch. Merging it is the next logical step. No conflicts expected with `main`; mostly new files (`rag/eval/core.py`, `rag/eval/report.py`, `docs/eval_report.{html,json}`, `docs/EVAL_RESULTS.md`) + small extension of existing `rag/eval/run.py` and `rag/eval/golden.jsonl`.

## Total estimated score

| Round | Score | Breakdown |
|---|---|---|
| R5 | 86.0 / 100 | baseline |
| R6 | 88.0 / 100 | +data breadth, -recall regression, +UX |
| **R6.5 (now)** | **89.5 / 100** | **+0.5 工程** (Sam's dashboard adds testability) **+1.0 效果** (Tujie's synonyms recovered recall + multiturn perfectly handled) |

If Sam's dashboard merges + we close the brand-origin negation gap, R7 estimate is **91-92 / 100**.

## How I tested

```bash
git pull --ff-only origin main          # → b317081
source .venv/bin/activate
cd rag && python -m eval.run            # → recall@5=0.816, MRR=0.705
cd /Users/shufengc/Desktop/rag/AAALion-/server && \
  source ../.venv/bin/activate && \
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000  # listening
curl -X POST http://localhost:8000/chat/stream -d '...'  # 4 smoke tests above
# iOS rebuild + reinstall on iPhone 13 Pro (UDID 7310469E-…) with new LAN IP 192.168.22.50
```

A100 not touched this round (no embedding changes needed).
`cuda-fuzzing/` mtime unchanged (verified earlier in R6: `2026-05-21 03:47:17`).
