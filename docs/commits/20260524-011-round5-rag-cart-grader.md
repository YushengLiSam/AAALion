# Round 5 — RAG depth + 4.1 cart + measured eval + grader self-assessment

**Date**: 2026-05-24
**SHA**: (fill after commit)
**Author**: Shufeng Chen <shufeng.c.dev@gmail.com>
**Branch**: `shufeng` (will fast-forward to `main` after user review)

## Why

User asked for "deep per PDF" — every bonus tier covered, measured numbers where things were unmeasured, full 4.1 cart implementation (not the stub variant), and an objective grader-style self-assessment at the end. Presentation work (video, slides) explicitly deferred.

## What changed

### Phase A — RAG depth (hybrid + rewrite + negation + rerank)
- `rag/retrieve/bm25.py` NEW — jieba-tokenized BM25 corpus over product catalog.
- `rag/retrieve/hybrid.py` NEW — RRF fusion of dense (Chroma) + BM25.
- `rag/retrieve/rewrite.py` NEW — LLM-driven query expansion for vague queries; cost-aware (skipped when query has specifics).
- `rag/retrieve/negation.py` NEW — extract `exclude_brands/categories/keywords` via LLM; apply as Chroma where-clause + post-filter.
- `rag/retrieve/rerank.py` REWRITE — `BAAI/bge-reranker-base` cross-encoder; lazy load.
- `server/app/services/rag_client.py` REWRITE — full pipeline: (optional) rewrite → hybrid → negation filter → rerank → top-k.

### Phase A5 — Measured eval
- `rag/eval/golden.jsonl` grown from 10 → 31 cases (19 with expected ids).
- `rag/eval/run.py` REWRITE — runs 3 modes (dense / hybrid / hybrid+rerank), reports recall@5, recall@10, MRR.
- **Measured numbers** (committed to `docs/QUALITY_REVIEW.md`):

  | Mode | recall@5 | recall@10 | MRR |
  |---|---|---|---|
  | dense | 0.605 | 0.816 | 0.585 |
  | hybrid | 0.632 | 0.711 | 0.501 |
  | **hybrid+rerank** | **0.711** | 0.763 | **0.695** |

### Phase B — Backend hardening
- `server/app/routes/chat.py` REWRITE:
  - Cache check + replay (services/cache.py wired in; ~15ms per-delta replay delay)
  - Tightened system prompt (vision commit rule, structured negation rule, 3-5 dim comparison)
  - Per-request structured timing log: `retrieval_ms`, `first_delta_ms`, `total_ms`, `cache: hit|miss`
  - Retry/backoff for upstream LLM errors (3 attempts, 0.5/1/2 s exp backoff)
  - Client-disconnect detection via `request.is_disconnected()` mid-stream → cancel
  - Intent detection: regex `加入购物车 / 加购` + `下单 / 结算 / 买单` → emit `cart_intent` SSE event for iOS
- Verified: cache miss `first_delta_ms=7893`, cache hit `first_delta_ms=318` (24× speedup on hit).

### Phase E — Full 4.1 cart + checkout (iOS)
- `client/AAALionApp/AAALionApp/Models/CartItem.swift` NEW — Codable + Hashable, line-total computed.
- `client/AAALionApp/AAALionApp/Stores/CartStore.swift` NEW — `@Observable`, UserDefaults-persisted; survives app relaunch.
- `client/AAALionApp/AAALionApp/Views/CartSheet.swift` NEW — list with +/− qty, swipe-to-delete, total, "去结算" button.
- `client/AAALionApp/AAALionApp/Views/CheckoutView.swift` NEW — review + mock address + "确认下单" → success screen with "继续购物" button.
- `ProductDetailView.swift` UPDATE — "加入购物车" button + toast confirmation.
- `ChatView.swift` UPDATE — cart icon (with badge) in toolbar; opens `CartSheet`; reacts to `cartIntent` from backend (auto-add last products on "加购", open cart on "下单").
- `Models/ChatDelta.swift` UPDATE — new `cart_intent` event type.
- `ViewModels/ChatViewModel.swift` UPDATE — exposes `cartIntent: String?` to ChatView.

### Phase G — Grader self-assessment
- `docs/QUALITY_REVIEW.md` NEW — full per-rubric scoring with measured evidence:
  - Total estimated score: **86.0 / 100**
  - 基础功能 94, 工程质量 88, 效果与可靠性 82, 加分项 73.5
  - "What I'm certain of vs uncertain of" section with honest caveats
  - "Honest verdict": B+ to A- on technical execution; gap to A is data scale + measured stress test + demo video

### Policy
- `docs/POLICY.md` UPDATE — added "From 2026-05-24 onwards" rule: commits land on `shufeng` first, FF merge to `main` only at end of iteration after self-assessment + user review.

## What was deferred (intentional)

- **Phase D** (real product curation + 2 new categories): time budget. Mentioned in QUALITY_REVIEW under "What I'd do with another week."
- **Phase B4** (stress test): scheduled for next round when locust setup time fits.
- **Phase F1** (pre-commit hook auto-install): one-liner, deferred to a doc note.
- Demo video / defense deck: user explicitly cut from this round.

## Verification

- `aaalion eval` reports the numbers above; reproducible.
- `aaalion ios-sim` builds; cart flow works in simulator.
- iPhone 13 Pro still has Round 4 build; will reinstall via `aaalion ios-device` after merge.
- `tools/check-secrets.sh` clean.
- `cuda-fuzzing/` on uc mtime unchanged.

## Follow-ups

- User reviews this commit + QUALITY_REVIEW; if approved, fast-forward merge `shufeng → main`.
- Reinstall on iPhone (`aaalion ios-device`).
- Send WeChat update (TBD; not in this commit).
- Optional Phase D in next round.
