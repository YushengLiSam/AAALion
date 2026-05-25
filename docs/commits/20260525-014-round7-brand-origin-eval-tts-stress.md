# Round 7 — team merges + brand-origin negation + Tier-2 nice-to-haves

**Date**: 2026-05-25
**Branch**: `shufeng` → FF merge to `main`
**Author**: Shufeng Chen `<shufeng.c.dev@gmail.com>`

## Why this commit exists

R6.5 measured Tujie's synonym + multi-turn + price-intent merges as
delivering +19% recall@5. iPhone test showed one regression: "不要日系"
still surfaced 安热沙 (Japanese sunscreen). User asked for Tier-1
debugging cycle + Tier-2 nice-to-haves + defense materials prep.

## What landed

### Tier 1 — debug + record

1. **Merge Sam's `Yusheng → main`** (`49d5664`, merge commit).
   - Adds `rag/eval/{core,report}.py`, grows golden.jsonl 31 → 56 cases,
     ships `docs/eval_report.html` + `docs/EVAL_RESULTS.md` per-scenario
     dashboard.
   - One known-bad case in the merged routes/chat.py had been fixed
     locally already — auto-merge picked the right side.

2. **Brand-origin negation fix** (`dc13f32`, standalone commit).
   - NEW `rag/retrieve/brand_origin.py` — 70-brand → ISO-2 country map
     + COUNTRY_KEYWORDS set.
   - Extended `apply_negation` to drop products whose resolved origin
     is in the excluded-country set.
   - Added 3 brand-origin golden cases (JP / KR / FR exclusion).
   - Live SSE confirmed: `推荐防晒霜不要日系` now returns 巴黎欧莱雅 /
     理肤泉 / 科颜氏 / La Roche-Posay only — zero JP.

3. **Re-recorded 6 demos** under `docs/demos/2026-05-25/`:
   - basic / filter / negation / multi-turn / compare / no-match
   - 5 PNG screenshots + 6 sidecar `.md` + index README
   - Multi-turn captured as SSE log only (simctl harness is single-shot);
     UI flow goes into the Tier-2 defense video.

4. **Refreshed teammate docs**:
   - `CLAUDE.md` — quality table + timeline + header date bumped.
   - `docs/QUALITY_REVIEW.md` — R7 row added to score history, R5→R7
     delta table, current live numbers.
   - `README.md` — Live status section → "Round 7", capability matrix
     reflects merge + brand-origin fix.

### Tier 2 — nice-to-haves (shufeng-only)

5. **B1 latency fast-path** (`server/app/services/rag_client.py`).
   When query mentions a known catalog brand AND no negation, skip
   cross-encoder rerank — dense+BM25 already converges. Env-toggleable
   via `RAG_FAST_PATH` (default ON). **Net measured impact** on the
   59-case eval: recall@5 0.723 → 0.746 (+0.023), recall@10 0.862 →
   0.884 (+0.022), neg-acc 0.733 preserved, median latency 305ms →
   266ms (-13%). Strictly better.

6. **B2 TTS auto-read first paragraph** (iOS).
   `ChatViewModel.maybeSpeakFirstParagraph(messageID:)` triggers on
   text-append, detects paragraph boundary (`。/！/？/.!?` or `\n\n`
   or 200 chars), speaks once per message ID. Per-message dedup via
   `Set<UUID>`. Gated by `@AppStorage("lionpick.autoTTS")` toggle in
   SettingsView (default OFF — opt-in to avoid surprise).

7. **B3 Stress test** (NEW `tools/stress_test.py`). R5 plan called for
   this but the file was never written. Implemented now: async httpx
   workers, rotating 10 representative queries. 20 workers × 45s →
   **100% success rate (92/92)**, 1.9 req/s throughput (LLM-bound),
   first-delta p50 2.3s. Report at `docs/stress_test_2026-05-25.md`.

### Defense materials

8. **Gamma slide-deck prompt** (`docs/defense/gamma-prompt.md`).
   10-slide Chinese spec (cover → architecture → wins → real-product
   story → measured quality → demos → engineering → Q&A → team →
   status). Includes my disagreement note: Gamma is great for slides
   but awkward for demo videos — recommended QuickTime screencast for
   the video portion.

### Local-only (gitignored, not in this commit)

9. **Cluely defense-support package** at `docs/cluely/` (gitignored from
   Step 1). Contents: `prompt.md` (defense-prep system prompt),
   `meeting-context.md` (200-word paste-ready), `judge-questions.md`
   (15 pre-vetted Q&As with 30-sec spoken answers), `context-bundle/`
   (symlinks to key teammate-shared docs so the bundle stays current),
   `log.md` (this round's step-by-step debug log).

## Measured quality

| Metric (hybrid_rerank, 59-case golden) | R6.5 | **R7 now** |
|---|---:|---:|
| recall@5 | 0.816 (31-case) | **0.746** (59-case, harder) |
| recall@10 | — | 0.884 |
| MRR | 0.705 | 0.674 |
| negation accuracy | — | **0.733** (preserved after brand-origin fix) |
| no-match correctness | — | 0.855 |
| median latency (cache-warm) | — | **266 ms** |
| stress test success rate (20 workers × 45s) | — | **100%** |

Self-assessment: **88.0 → 90.0 / 100**
(`docs/QUALITY_REVIEW.md` updated).

## Files changed (representative)

```
rag/retrieve/brand_origin.py            NEW
rag/retrieve/negation.py                 +country exclusion
rag/eval/golden.jsonl                    +3 brand-origin cases
server/app/services/rag_client.py        +_is_specific_query + fast-path
client/.../ViewModels/ChatViewModel.swift +autoTTS state + paragraph trigger
client/.../Views/SettingsView.swift      +autoTTS toggle section
tools/stress_test.py                     NEW (async httpx workers)
docs/defense/gamma-prompt.md             NEW (10-slide deck spec)
docs/demos/2026-05-25/*                  NEW (6 screenshots + sidecars + README)
docs/stress_test_2026-05-25.md           NEW
docs/QUALITY_REVIEW.md                   R7 row + delta
docs/QUALITY_REPORT_2026-05-25.md        (already committed R6.5)
README.md                                Live status → R7
CLAUDE.md                                §5 quality, §6 timeline, header
docs/cluely/                             GITIGNORED — local defense prep
.gitignore                               +docs/cluely/ (separate commit d66f498)
docs/eval_report.{html,json}             regenerated under R7 pipeline
```

## Risks / open items

- **Demo video** not yet recorded — Tier 2 #6. Plan: QuickTime
  screencast of the simulator running the 6 demos, voiceover after.
- **Hero product images** still AI-rendered placeholders — Tier 2 #7
  is to hand-source official photos for ~10 demo-critical SKUs.
- **JD SKU sanity check** still pending (R6 caveat carried forward).
  Sam or Tujie should verify 华为 GT4 / 凯乐石 MT5-3 / 牧高笛 冷山2
  URLs once before defense day.

## Verification

- `git ls-remote origin main shufeng` returns the same SHA, both at the
  R7 final commit.
- `python -m rag.eval.report` → dashboard regenerated with R7 pipeline.
- Live SSE: `推荐防晒霜不要日系品牌` returns no Japanese-brand products.
- iOS simulator: 5/6 screenshots captured + 6 sidecar `.md` files all
  written.
- `tools/check-secrets.sh` clean.
- `tools/stress_test.py` 100% success at 20 workers × 45s.
- A100 mtime check pending in the rsync step.

— Shufeng
