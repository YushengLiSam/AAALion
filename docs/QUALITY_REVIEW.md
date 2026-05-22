# 狮选 LionPick — Quality Self-Assessment (2026-05-24)

> An objective, grader-style review written by the implementer at the end of Round 5.
> No marketing fluff. Each rubric item gets a target weight, achieved score (0-100),
> evidence link, gap statement, and what would push the score higher.

## Total estimated score: **86.0 / 100**

| Dimension | Weight | Score | Weighted |
|---|---|---|---|
| 基础功能完整性 | 35% | 94 | 32.9 |
| 工程质量 | 25% | 88 | 22.0 |
| 效果与可靠性 | 20% | 82 | 16.4 |
| 加分项 | 20% | 73.5 | 14.7 |
| **Total** | **100%** | — | **86.0** |

## Methodology

- Each item scored 0-100 against the PDF §7.1 rubric criteria.
- Evidence is a working code/doc/demo link; if I can't link to it, the score reflects that.
- I am the implementer and grader — there is selection bias. I've tried to be harsher than a friendly reviewer would be on items where the evidence is thin.
- "What would push it higher" is the actionable backlog if this were a Round 6.

---

## 基础功能完整性 — 94 / 100 (weight 35%)

| Sub-item | Score | Evidence | Gap | Push higher |
|---|---|---|---|---|
| iOS native client (no H5) | 100 | SwiftUI 17+, `client/AAALionApp/`; verified on iPhone 13 Pro | none | — |
| Streaming chat (SSE) | 100 | `server/app/routes/chat.py` + `ChatService.swift`; verified end-to-end | none | — |
| RAG retrieval | 100 | hybrid dense + BM25 + cross-encoder rerank; `rag/retrieve/` | none | — |
| Product cards with images | 95 | `ProductCardView.swift`; images load after Round 4 URL fix | placeholder when image 404 | preload sizes |
| Multi-turn conversation | 95 | iOS sends full history; edit-message UX | rolling-window pruning not implemented (would degrade after ~20 turns) | sliding window + summarization |
| Catalog grounding (no hallucination) | 90 | Demo 02 shows "no match" honesty; system prompt enforces | rare leakage when LLM paraphrases | LLM-as-judge post-check |

**Weakness I'd flag if I were the grader**: the catalog is only 100 products in 4 categories. A vague query that doesn't intersect any of those (e.g. "助孕用品") returns "no match" honestly, but the demo would feel richer with breadth. Round 5 deferred the new-category work.

---

## 工程质量 — 88 / 100 (weight 25%)

| Sub-item | Score | Evidence | Gap | Push higher |
|---|---|---|---|---|
| Code structure | 95 | `client/ server/ rag/` separation; `docs/ARCHITECTURE.md` | — | — |
| API design | 90 | `docs/API.md`; Pydantic v2 content union; SSE event taxonomy | no OpenAPI spec auto-published | FastAPI publishes `/openapi.json` but I don't surface it |
| Error handling | 88 | SSE error events; iOS error banner; LLM provider retry/backoff (3× exp backoff); `LLM_PROVIDER=echo` graceful fallback | no client retry on network blip | iOS reconnect logic |
| Private deployment | 85 | `server/Dockerfile` + `docker-compose.yml`; works locally | not verified by me running `docker compose up` from clean clone | live verification |
| Multi-provider LLM | 100 | TokenRouter / Anthropic / Doubao / OpenAI / Echo via env switch | — | — |
| Documentation | 95 | 24 docs in `docs/`, `IMPLEMENTATION_GUIDE.md` indexes them, 10 commit records, `RUBRIC_MAPPING.md`, this file | — | — |
| Repo hygiene | 95 | Conventional Commits; major-commit records; secret scanner; gitignore covers `.chroma/` + `.env` + `xcodeproj` | no pre-commit hook installed yet | wire `tools/check-secrets.sh` as pre-commit |
| Latency instrumentation | 85 | `server/app/routes/chat.py` `_log_timing`; JSON-per-request with `retrieval_ms` / `first_delta_ms` / `total_ms` / `cache` | no aggregate dashboard | publish Prometheus metrics |
| Cache layer | 80 | `services/cache.py` in-memory LRU 200-entry × 10-min TTL; wired into chat route; cache hit reduces `first_delta_ms` from ~5000 → ~300 in measurement | no eviction on data change | invalidate cache on `aaalion ingest` |
| Stress test | 60 | `tools/stress_test.py` deferred to next round | no p95/p99 numbers | run locust 100 RPS × 60s |
| Git workflow | 90 | shufeng branch for in-flight; main = stable; merge after self-assessment | no PR template enforcement | wire `.github/pull_request_template.md` more rigorously |

**Weakness**: the stress test is the biggest gap. I can claim cache + latency targets are met from single-shot measurements, but I haven't verified under load.

---

## 效果与可靠性 — 82 / 100 (weight 20%)

| Sub-item | Score | Evidence | Gap | Push higher |
|---|---|---|---|---|
| 检索准确率 (recall@5) | 85 | Measured: dense=0.605, hybrid=0.632, **hybrid+rerank=0.711** on 31-case golden set. MRR 0.585 → 0.695. | only 31 cases; 12 have no expected ids (intentional "no-match" tests) | grow to 80+ cases; add human-judged relevance scores |
| 无幻觉输出 | 90 | System prompt enforces; demo 02 proves; Round 5 vision-prompt tightening | no automated hallucination check | LLM-as-judge nightly |
| 复杂场景 (negation, comparison) | 90 | demos 04 + 05; Round 5 added structured negation extraction → filter | rare LLM still hedges | tighten more |
| First-screen response (<1s target) | 80 | Cache hit: ~300ms first_delta. Cache miss: ~3000-8000ms first_delta (mostly LLM-side, not our overhead) | cache miss path doesn't meet <1s target | prefetch on app focus, preload model weights |
| Image input pipeline | 85 | Photos + Camera + Files (all 3 sources work); CLIP retrieval on A100 (100 images, 512-d vectors) | A100 not used for live retrieval — Mac runs CLIP on MPS at ~50ms per image | move CLIP serving to A100 over SSH tunnel for true GPU offload |
| Voice input quality | 80 | Apple Speech.framework zh-CN; works in demos | no continuous listening; no interrupt | streaming partial results during recognition |
| TTS quality | 75 | AVSpeechSynthesizer zh-CN system voice | flat prosody; no SSML | use a neural TTS if budget allows |

**Weakness**: recall@5 of 0.711 is decent but not "high-end product" territory. The single biggest lever would be a properly-tuned cross-encoder (we use the base model; the v2-m3 variant is bigger and better) and 50+ more golden eval cases.

---

## 加分项 — 73.5 / 100 (weight 20%)

### 4.1 业务闭环深度 — 70

| Tier | Score | Evidence |
|---|---|---|
| ⭐ 对话式加购 | 100 | Round 5: `_detect_cart_intent` regex in `chat.py` + iOS `ChatView.onChange(cartIntent)` auto-adds last assistant's products |
| ⭐⭐ 购物车管理 | 90 | `CartSheet.swift` — list, +/− qty, swipe-to-delete, total, persisted via UserDefaults |
| ⭐⭐⭐ 下单确认流程 | 65 | `CheckoutView.swift` mock flow: review → confirm → success. **Mock only** — no real payment / shipping / inventory. Honest framing in UI ("演示用模拟下单"). |

**Weakness**: checkout is mock. A real impl needs an order-id-issuing backend route and order history. That's another half-day; out of scope.

### 4.2 多模态交互 — 90

| Tier | Score | Evidence |
|---|---|---|
| ⭐ 语音输入 | 95 | `SpeechService.swift` with `SFSpeechRecognizer(locale: zh_CN)`; partial-result streaming into draft |
| ⭐⭐ TTS | 80 | `TTSService.swift` with `AVSpeechSynthesizer`; system Mandarin voice — quality is OK but obviously synthetic |
| ⭐⭐⭐ 拍照找货 | 95 | **TWO paths**: (a) vision LLM (`claude-haiku-4-5` via TokenRouter), (b) CLIP image embedding on A100 (100-vector `products_image` Chroma collection). `routes/chat.py` prefers CLIP when image is uploaded. Demo `docs/demos/2026-05-23/06-photo-clip.png` |

**Weakness**: the A100 indexed the catalog images, but the live retrieval runs on the Mac (MPS backend). Pure A100 serving would be cleaner but adds infra; current path is correct for solo dev.

### 4.3 对话智能 — 92

| Tier | Score | Evidence |
|---|---|---|
| ⭐ 多轮上下文记忆 | 95 | Full history sent on each turn; edit-message rolls history back; demo 03 |
| ⭐⭐ 反选与排除 | 90 | Round 5: `rag/retrieve/negation.py` extracts `exclude_brands/categories/keywords` via LLM, applies as Chroma where + post-filter. Plus the system-prompt rule for belt-and-braces. |
| ⭐⭐⭐ 多商品对比 | 92 | Demo 05: 雅诗兰黛 vs 兰蔻 returns 4-dim comparison; system prompt forces dimension selection |

**Weakness**: comparison is prompt-driven; the model occasionally picks 2 dimensions instead of 3-5. Could deterministically extract entities + dimensions client-side and present as a table.

### 4.4 工程质量 — 70

| Tier | Score | Evidence |
|---|---|---|
| ⭐ 热门查询缓存 | 95 | Round 5: wired `services/cache.py` into chat route; verified cache HIT drops first_delta from 7893ms → 318ms in our test |
| ⭐⭐ 首屏极速响应 | 70 | Cache hit ≤ 500ms (good); cache miss 3-9s (LLM-side, hard to compress further without a faster model). Typing-dots placeholder gives ≤100ms visible feedback. |
| ⭐⭐⭐ 端侧体验打磨 | 90 | Claude-designed theme, generated lion icon, empty state, typing dots, skeleton placeholders, context menus, settings sheet — all shipped |

**Weakness**: cache miss path can't reach <1s without a fundamentally faster LLM. Acknowledged trade-off.

---

## 减分项 (PDF §7.3) — self-check

| Risk | Status | Defense |
|---|---|---|
| AI 编造不存在的商品 | ✅ avoided | System prompt + demo 02 + 反幻觉 prompt explicit; no observed instance |
| Web/H5 替代原生 | ✅ avoided | Pure SwiftUI; verified on iPhone 13 Pro device |
| Demo 无法跑 | ✅ avoided | Reproducibility: `aaalion ios-sim` + `aaalion backend` + 9 demo screenshots committed; weekly cert re-sign cadence documented |
| 不能解释原理 | ✅ avoided | This file + `docs/RUBRIC_MAPPING.md` + `docs/HONEST_ANSWERS.md` + `docs/IMPLEMENTATION_GUIDE.md` |

---

## What I'd do with another week

In priority order, if defense were 7 days further out:

1. **Grow golden eval to 80+ cases**; measure recall vs human-judged relevance, not just expected-id match.
2. **Real product data**: hand-curate 30 real Tmall/JD entries; index alongside the AI-gen seed. Run side-by-side eval.
3. **Stress test**: locust 100 RPS × 60s with the cache wired — verify the p95 claim.
4. **bge-reranker-v2-m3**: ~2x the size, ~5-10% better on Chinese retrieval benchmarks per the model card. ~1 hour.
5. **Demo video** (Phase explicitly deferred this round per user instruction).
6. **Defense slide deck** (same).
7. **Bonjour discovery** of the backend so LAN URL changes are automatic.

---

## What I'm certain of vs uncertain of

**Certain**:
- Recall@5 = 0.711 on 31 cases. Reproducible: `aaalion eval`.
- Cache hit drops first_delta ≥ 10×. Measured.
- iPhone 13 Pro deploy works. Hands-on tested.
- All listed 4.x bonus items have a working code path.

**Uncertain**:
- Whether 0.711 recall@5 is "good" by absolute benchmark — RAG papers report 0.8+ on larger corpora; ours is small (100 products, ~50% of queries are no-match-expected).
- Whether the cart's regex-based intent detection holds against weird phrasings — robust would be a proper intent classifier.
- Whether the cross-encoder reranker's first-call latency (~3s model load) will surface as a bad first-impression in the demo.

## Honest verdict

This is a **defensible AI 全栈** submission. Every PDF rubric item except 4.1 has substantial coverage; 4.1 is at "honest mock" depth. Real weaknesses are: small catalog, no live stress test, mock checkout. Nothing in the build is fake or hand-wavy — every claim in `RUBRIC_MAPPING.md` resolves to code or a measured number.

If I had to give my own grade: **B+ to A-** on technical execution. The gap to A is the data scale + measured stress test + a polished demo video.
