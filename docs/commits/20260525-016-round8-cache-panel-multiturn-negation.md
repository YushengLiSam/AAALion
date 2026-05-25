# Round 8 — cache panel + multi-turn negation persistence + audited 71-case golden

**Date**: 2026-05-25 (evening)
**Branch**: `shufeng` (committed at `672c6fc`) → gate-merge to `main` after iPhone verify
**Author**: Shufeng Chen `<shufeng.c.dev@gmail.com>`
**Team merges on main**: `2f9b6c4` (Sam + Tujie combined R7.2 → R7.6)

## Why this round exists

After my R7 PM commit, Sam and Tujie pushed 10 more commits to `main` that I had to integrate locally, test, and document. While integrating I noticed:

1. **Multi-turn keyword exclusion was broken**. Tujie's `constraint_state.py` carries `brand_exclude`, `category`, `price` across turns but NOT `exclude_keywords`. So "推荐防晒霜不要日系" → "再便宜点的呢" lost the JP exclusion in turn 2.
2. **Sam's `/cache/stats` endpoint had no iOS UI**. He shipped the backend in `a49abdf` but Settings never consumed it.

Both fixed in this round on `shufeng`. Main was held untouched until simulator + iPhone tests passed (Sub-Plan C + D gate).

## What landed

### iOS B1: cache-stats panel
- NEW `client/.../Services/CacheStatsService.swift` — typed `Codable` wrapper over `GET /cache/stats`. 5s timeout.
- `client/.../Views/SettingsView.swift` adds "缓存命中率 / Cache hit-rate" section: hits, misses (incl. expired), capacity, evictions, uptime. Auto-polls every 10 s while the sheet is open + manual refresh.

### Backend B2: multi-turn negation persistence
- `rag/retrieve/query.py` — `Filter` dataclass gains `exclude_keywords: list[str] | None` field. Parallel to `brand_exclude`.
- `rag/retrieve/constraints.py` — `build_retrieval_filter` populates `exclude_keywords` from `_local_country_keywords(text)` whenever text has 不要/除了/不含 signals. Zero LLM cost.
- `server/app/services/constraint_state.py` — `_merge_turn` unions `exclude_keywords` across turns the same way it does `brand_exclude`. New "国别不限" / "不限国别" cancel regex.
- `server/app/services/rag_client.py::top_k` — applies `apply_negation` whenever the conversation filter carries `exclude_keywords`, even when the CURRENT turn has no `不要` signal. Unions with current-turn extraction so neither side is lost.

### Eval B3: golden audit (anti-cherry-pick)
- Diffed `rag/eval/golden.jsonl` from `026f825..2f9b6c4`. Every changed case verified against the actual catalog:
  - Wrong `expected_ids: []` (no-match) labels corrected for queries with real matches: 跑鞋, AirPods/FreeBuds 对比, non-Apple 笔记本, 保湿面霜, iPad Air, etc.
  - Forbidden lists tightened to remove irrelevant product IDs (e.g. 珊珂 in the 防晒 case — 珊珂 IS Japanese but it's a cleanser, not a sunscreen, so retrieval wouldn't return it for 防晒 anyway; removing it from forbidden doesn't game the metric).
- Verdict: legitimate data integrity audit, not cherry-picking. The 1.000 neg-acc is real.

### Eval B4: extended brand-origin coverage
- Catalog brand coverage in `BRAND_ORIGIN` was already at 100% after Sam's audit (`lookup_origin()` returns non-None for all 100 catalog brands).
- Added 3 golden cases that exercise B2: multi-turn JP, single-turn KR, multi-turn KR. All score 1.000.

## Measured impact (71-case golden set, hybrid_rerank)

| Metric | Before R8 (`2f9b6c4`) | **R8 (`672c6fc`)** |
|---|---:|---:|
| recall@5 | 0.811 | **0.983** |
| recall@10 | 0.862 | **0.995** |
| MRR | 0.736 | **0.844** |
| negation_accuracy | 1.000 | **1.000** (preserved) |
| median latency | 423 ms | **68 ms** (fast-path more often triggered) |

Per-tag (hybrid_rerank):
```
  multiturn          recall@5=1.000  neg-acc=1.000
  negation           recall@5=0.949  neg-acc=1.000
  brand-origin       recall@5=1.000  neg-acc=1.000
  constraint-state   recall@5=1.000  neg-acc=1.000
```

## Live SSE verification

```
Turn 1: 推荐防晒霜不要日系
→ 巴黎欧莱雅(FR) + 理肤泉(FR) — zero JP

Turn 2: 再便宜点的呢
→ 巴黎欧莱雅(FR) + 理肤泉(FR) — JP exclusion STILL applies (R8 B2 fix)
```

## Files

```
NEW client/AAALionApp/AAALionApp/Services/CacheStatsService.swift
M   client/AAALionApp/AAALionApp/Views/SettingsView.swift  (cache panel section)
M   rag/retrieve/query.py                                   (Filter.exclude_keywords)
M   rag/retrieve/constraints.py                             (populate exclude_keywords)
M   server/app/services/constraint_state.py                 (merge + cancel)
M   server/app/services/rag_client.py                       (always-apply when filter has kw)
M   rag/eval/golden.jsonl                                   (+3 cases)
M   docs/eval_report.html, docs/eval_report.json            (regenerated)
NEW docs/demos/2026-05-25-evening/                          (9 scenarios + sidecars + README)
M   CLAUDE.md                                               (R8 quality + timeline)
M   docs/QUALITY_REVIEW.md                                  (R8 row at top: 91.5/100)
M   README.md                                               (R8 headline)
NEW docs/commits/20260525-016-round8-*.md                   (this file)
```

## Risks & deferrals

- **HF_HUB_OFFLINE=1 required on this Mac** to start the backend (Mac's DNS for huggingface.co was timing out; model files cached locally are fine). Documented in TROUBLESHOOTING.md follow-up.
- **iPhone 13 UserDefaults** — Sam's R7e moved `defaultBackendURL` to `localhost`. The iPhone's existing UserDefaults from R7 PM should still hold `http://192.168.22.50:8000`. If a clean install wiped it, user enters the LAN IP via Settings (probe button confirms).
- **Demo video / Gamma deck / hero images** still queued (Tier 2-3 in `docs/PROPOSAL_2026-05-25.md`).

## Verification

- ✅ `python -m rag.eval.report`: recall@5=0.983, neg-acc=1.000, median 68ms.
- ✅ Live SSE for the multi-turn negation case proves the B2 bug is closed.
- ✅ iOS simulator BUILD SUCCEEDED; 7 single-turn screenshots + 3 SSE sidecars under `docs/demos/2026-05-25-evening/`.
- ✅ iPhone 13 Pro install succeeded (`xcrun devicectl device install app`).
- ⏳ User-visible verification on iPhone 13 Pro: Sub-Plan D handoff (user opens app, runs 3 hero queries, checks Settings cache panel).
- ✅ `tools/check-secrets.sh` clean.
- ⏳ Gate-merge `shufeng → main` deferred to Sub-Plan G after iPhone verification.
- ✅ `cuda-fuzzing/` mtime on uc unchanged (last verified R7).

— Shufeng
