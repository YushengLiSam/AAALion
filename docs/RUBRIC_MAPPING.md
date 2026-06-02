# Rubric Mapping — ByteDance §4/§7 → Code/Artifact

Explicit map from each ByteDance 2026 AI 全栈挑战赛 rubric item to a concrete
artifact in this repo, with **verification status** (coded vs. live-verified).
Use this in defense Q&A. Last refreshed: **R10 (2026-06-01)** — reflects the
cloud deploy, the cart depth (4.1), and the latency/observability work (4.4).

> Verification legend: **✅ live-verified** = exercised on the running system
> (cloud or device) this round · **✅ coded** = implemented + builds, not
> re-demoed this round · 🟡 partial · ⏳ deferred (with rationale).

---

## 评分维度 (§7.1)

### 基础功能完整性 (35%)

| Sub-item | Status | Artifact / evidence |
|---|---|---|
| 客户端对话 | ✅ live | `client/.../Views/ChatView.swift` — SwiftUI native, runs on iPhone 13 Pro + simulator |
| 后端 RAG 检索 | ✅ live | hybrid dense(bge-small-zh)+BM25 → RRF → cross-encoder rerank; `rag/retrieve/{hybrid,bm25,rerank,query}.py`; Chroma `products_text` (1082 chunks). Multi-turn category/brand/RMB filters: `services/constraint_state.py` + `contextual_query.py` |
| 模型生成 | ✅ live | `services/llm_provider.py` — TokenRouter `claude-haiku-4-5` (multimodal, OpenAI-compatible); one-env-var swap to Doubao/方舟 |
| 流式返回 | ✅ live | SSE in `routes/chat.py`; cart_intent / product_card / delta / claim_summary / done events; iOS decode in `Services/ChatService.swift` |
| 商品卡片展示 | ✅ live | `Views/ProductCardView.swift` (flag badge, add-pill, **heart**), relative-URL resolution in `Models/ProductCard.swift` |

### 工程质量 (25%)

| Sub-item | Status | Artifact / evidence |
|---|---|---|
| 代码结构清晰 | ✅ | `client/ server/ rag/ docs/` separation; `docs/ARCHITECTURE.md` |
| 接口设计合理 | ✅ | `docs/API.md`; Pydantic v2 content-union schema; documented SSE event taxonomy |
| 错误处理完善 | ✅ live | SSE error events + retry/backoff (`_stream_chat_with_retry`); iOS error banner; `/ready` gate blocks chat before model warmup; echo-provider fallback |
| **部署 / 运维** | ✅ live | **GCP VM + systemd**, public HTTPS via Cloudflare tunnel; **CD: push→main auto-deploys in ~2 min with `/ready` check + auto-rollback** (`tools/cloud-autodeploy.sh`, ready-window hardened to 150 s after a false-rollback bug was found+fixed). Zero code-drift verified (deployed == origin/main) |
| **可观测性** | ✅ live | `GET /cache/stats` surfaces **both** cache layers' hit-rates (response + retrieval); iOS Settings panel renders them |
| 文档齐全 | ✅ | `docs/`: ARCHITECTURE, PIPELINE, DEPLOY_GUIDE, TROUBLESHOOTING, this mapping, COMPETITIVE_ANALYSIS, PROPOSAL, DEV_LOG |

### 效果与可靠性 (20%)

| Sub-item | Status | Artifact / evidence |
|---|---|---|
| 运行流畅 | ✅ live | cloud backend live; cards stream sub-1s on cache hit (measured) |
| 界面美观 | ✅ | Claude-designed tokens (`design-tokens.json`), warm-ivory theme, generated lion icon, SF Pro Rounded, skeleton loading, spring micro-interactions |
| 检索准确率 | ✅ live | golden-set (59 cases) **recall@5 = 0.964 / MRR = 0.817** (full-recall config); **0.941 / 0.816** with the latency-optimized rerank knobs deployed. Run: `python -m rag.eval.run` |
| 无幻觉输出 | ✅ live | `_PROMPT` in `routes/chat.py` enforces catalog-only; per-claim `[目录✓]/[推断?]` provenance markers rendered in-bubble; honest "无匹配" path |
| 复杂场景处理 | ✅ live | negation (`除了耐克`→0 Nike, live), comparison (markdown table, live), multi-turn relative refine (`再便宜点`→avg ¥362→¥238 same category, live) |

---

## 加分项 (20%, §4)

### 4.1 业务闭环深度 (购物车与下单) — **all tiers shipped**

| Tier | Status | Evidence |
|---|---|---|
| ⭐ 对话式加购 | ✅ live | `_detect_cart_intent` add path → `cart_intent` SSE → iOS `CartStore.add`; inline + pill on every card |
| ⭐⭐ 购物车管理 | ✅ live | **conversational quantity** `把数量改成2`/`第二个改成3个` (`_parse_set_quantity`→`CartStore.setQuantity`, live-verified on cloud) + **conversational delete** `删掉第二个` (`_REMOVE_FROM_CART`+ordinal) + swipe-to-delete + stepper |
| ⭐⭐⭐ 下单确认流程 | ✅ | `Views/CheckoutView.swift` — address confirm + line-item summary + CNY total + mock "下单完成"; cart_intent `checkout` opens it from chat |

### 4.2 多模态交互能力

| Tier | Status | Evidence |
|---|---|---|
| ⭐ 语音输入 (ASR) | ✅ | `Services/SpeechService.swift` (Speech.framework zh-CN, partial results, silence auto-stop) + mic button |
| ⭐⭐ TTS 语音播报 | ✅ | `Services/TTSService.swift` (AVSpeechSynthesizer zh-CN) + Speak menu |
| ⭐⭐⭐ 拍照找货 | ✅ live | **CLIP ViT-B/32 image→image** retrieval over `products_image` (145 vectors) — live-verified on cloud (Nike-tee photo → exact 1.000 + similar). Image **also** goes to the multimodal LLM for attribute grounding. `rag/retrieve/query.py:query_image`, `rag/ingest/embed_image.py` |

### 4.3 对话智能与 RAG 增强

| Tier | Status | Evidence |
|---|---|---|
| ⭐ 多轮上下文记忆 | ✅ live | `build_conversation_filter` + `build_retrieval_query`; `再便宜点的呢` carries category + drops avg price (live-verified) |
| ⭐⭐ 反选与排除 | ✅ live | `rag/retrieve/negation.py` (不要/除了/不含/排除); **golden negation accuracy = 1.000**; `除了耐克` → 特步/安踏/阿迪, 0 Nike (live) |
| ⭐⭐⭐ 多商品对比决策 | ✅ live | `_is_comparison_query` + system-prompt table directive (价格/成分/场景/优劣势); `对比防晒霜` → markdown table (live) |

### 4.4 工程质量与性能优化

| Tier | Status | Evidence |
|---|---|---|
| ⭐ 热门查询缓存 | ✅ live | **two layers**: response cache (`services/cache.py`, TTL 600s) + **retrieval cache** (`rag_client._heavy_retrieve` memo, TTL 300s). Measured 17.9s→**0.3s** on a repeat; both hit-rates exposed at `/cache/stats` |
| ⭐⭐ 首屏极速响应 | 🟡→✅ live | **cards-first pipeline** (product cards emitted before the LLM text — pure reorder, recall unchanged) + cached provider connection + rerank knobs (3.8× faster). 首屏: **0.3s cache-hit / 0.14–2.2s cold**, leading the LLM text by ~1s. Strict sub-1s LLM-token is met warm/cached; cold floor is the upstream LLM (CPU-VM honest limit) |
| ⭐⭐⭐ 端侧体验打磨 | ✅ live | **skeleton (骨架屏)** shimmer placeholders, **收藏 ❤️** heart with spring bounce + haptic (UserDefaults `FavoritesStore`), **滑动** cart swipe-actions (delete / favorite); 18× `withAnimation`, springs, scaleEffect, haptics |

---

## 减分项 (§7.3) — checks

| Risk | Status | Defense |
|---|---|---|
| AI 编造不存在的商品 | ✅ avoided | `_PROMPT` catalog-only + per-claim provenance markers; honest "无匹配" |
| 使用纯 Web/H5 替代原生 App | ✅ avoided | SwiftUI native iOS 17+, runs on physical iPhone 13 Pro (Personal Team signed) |
| Demo 无法正常运行 | ✅ hardened | **cloud backend** (no laptop dependency) + CD auto-rollback; demo warmup pre-caches queries → sub-1s. **Risk note**: public URL is a Cloudflare quick-tunnel (stable while up; named-tunnel upgrade is the one open item) |
| 完全依赖 AI 生成而无法解释原理 | ✅ avoided | this mapping + `docs/ARCHITECTURE.md` + `docs/COMPETITIVE_ANALYSIS_2026-05-30.md` explain each design choice |

---

## Reproduction recipe (for judges)

```bash
git clone https://github.com/YushengLiSam/AAALion-.git && cd AAALion-
brew install xcodegen
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example server/.env          # set TOKENROUTER_API_KEY
aaalion ingest                       # 1082 text chunks + 145 image vectors
aaalion backend &                    # http://localhost:8000  (or use the live cloud URL)
aaalion ios-sim                      # iPhone 17 Pro simulator
python -m rag.eval.run               # recall@5 / MRR / negation-accuracy table
```

Defense flow: run the live demo scenarios, each citing the rubric item it
covers; show `/cache/stats` for the latency story; show the golden-eval table
for the accuracy story.
