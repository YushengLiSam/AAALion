# Rubric Mapping — PDF §4 → Code/Artifact

Explicit map from each ByteDance PDF rubric item to a concrete artifact in this repo. Use this in defense Q&A.

## 评分维度 (PDF §7.1)

### 基础功能完整性 (35%)

| Sub-item | Status | Artifact |
|---|---|---|
| 客户端对话 | ✅ | `client/AAALionApp/AAALionApp/Views/ChatView.swift` |
| 后端 RAG 检索 | ✅ | `rag/retrieve/query.py` + Chroma `products_text` (992 chunks) |
| 模型生成 | ✅ | `server/app/services/llm_provider.py` — TokenRouter `claude-haiku-4-5` |
| 流式返回 | ✅ | SSE in `server/app/routes/chat.py` + `client/.../ChatService.swift` |
| 商品卡片展示 | ✅ | `client/.../Views/ProductCardView.swift` + relative-URL resolution in `Models/ProductCard.swift` |

### 工程质量 (25%)

| Sub-item | Status | Artifact |
|---|---|---|
| 代码结构清晰 | ✅ | `client/ server/ rag/ docs/` separation; `docs/ARCHITECTURE.md` |
| 接口设计合理 | ✅ | `docs/API.md`; Pydantic v2 content-union schema; SSE event types documented |
| 错误处理完善 | ✅ | SSE error events; iOS error banner; `LLM_PROVIDER` echo fallback |
| 文档齐全 | ✅ | 14 docs in `docs/`: ARCHITECTURE, PIPELINE, DEPLOY_GUIDE, TROUBLESHOOTING, RUBRIC_MAPPING, etc. |

### 效果与可靠性 (20%)

| Sub-item | Status | Artifact |
|---|---|---|
| 运行流畅 | ✅ | All 6 Round 2 demos + 3 Round 3 demos PASS |
| 界面美观 | ✅ | Claude-designed tokens (`design-tokens.json`), warm-ivory theme, generated lion icon, SF Pro Rounded |
| 检索准确率 | ✅ | bge-small-zh + CLIP; recall@5 measurable via `aaalion eval` |
| 无幻觉输出 | ✅ | `rag/prompts/system.md` + demo 02 (`02-conditional-filter.md`) shows honest "no match" |
| 复杂场景处理 | ✅ | demos 04 (negation), 05 (comparison), 06 (photo) — all Round 2 |

## 加分项 (20%, PDF §4)

### 4.1 业务闭环深度 (购物车与下单)

| Tier | Status | Notes |
|---|---|---|
| ⭐ 对话式加购 | ⏳ deferred | not in scope this round per `docs/POLICY.md` |
| ⭐⭐ 购物车管理 | ⏳ deferred | — |
| ⭐⭐⭐ 下单确认流程 | ⏳ deferred | — |

### 4.2 多模态交互能力

| Tier | Status | Artifact |
|---|---|---|
| ⭐ 语音输入 | ✅ | `client/.../Services/SpeechService.swift` (Speech.framework, zh-CN) + mic button in `ChatView.swift` |
| ⭐⭐ TTS 语音播报 | ✅ | `client/.../Services/TTSService.swift` (AVSpeechSynthesizer) + long-press Speak menu in `MessageBubbleView.swift` |
| ⭐⭐⭐ 拍照找货 | ✅✅ | TWO independent paths: (a) vision LLM via TokenRouter `claude-haiku-4-5`, (b) **CLIP ViT-B/32 on A100** indexing 100 product images into `products_image` Chroma collection; backend `routes/chat.py` prefers CLIP when image is uploaded. See `rag/ingest/embed_image.py`, `docs/demos/2026-05-23/06-photo-clip.png`. |

### 4.3 对话智能与 RAG 增强

| Tier | Status | Artifact |
|---|---|---|
| ⭐ 多轮上下文记忆 | ✅ | full history in messages array; `ChatViewModel.send()` sends all prior turns |
| ⭐⭐ 反选与排除 | ✅ | `rag/prompts/system.md` negation rule + demo `docs/demos/2026-05-22/04-negation.md` |
| ⭐⭐⭐ 多商品对比决策 | ✅ | demo `docs/demos/2026-05-22/05-comparison.md` (4-dim A-vs-B) |
| **(bonus)** edit-last-message UX | ✅ | long-press → Edit; rolls back history. Standard ChatGPT/Claude pattern. |

### 4.4 工程质量与性能优化

| Tier | Status | Artifact |
|---|---|---|
| ⭐ 热门查询缓存 | 🟡 partial | `server/app/services/cache.py` — LRU with TTL implemented; route integration deferred |
| ⭐⭐ 首屏极速响应 (<1s) | ✅ | streaming starts immediately; typing-dots placeholder in `MessageBubbleView.swift` makes <100ms feedback |
| ⭐⭐⭐ 端侧体验打磨 | ✅ | new icon, palette, typography, empty state, skeleton card placeholders, smooth scroll-to-bottom |

## 减分项 (PDF §7.3) — checks

| Risk | Status | Defense |
|---|---|---|
| AI 编造不存在的商品 | ✅ avoided | `rag/prompts/system.md` enforces; demo 02 proves model says "无匹配" honestly |
| 使用纯 Web/H5 替代原生 App | ✅ avoided | SwiftUI native, iOS 17+, runs on physical iPhone 13 Pro (signed via Personal Team) |
| Demo 无法正常运行 | ✅ avoided | all 9 demos screenshot-recorded; `aaalion ios-sim` and `aaalion ios-device` reproducible |
| 完全依赖 AI 生成而无法解释原理 | ✅ avoided | this RUBRIC_MAPPING + `docs/HONEST_ANSWERS.md` + `docs/ARCHITECTURE.md` show each design choice explained |

## Reproduction recipe (for judges)

```bash
git clone https://github.com/YushengLiSam/AAALion-.git
cd AAALion-
brew install xcodegen
make install-cli      # OR: ln -sf $(pwd)/tools/aaalion ~/.local/bin/aaalion
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example server/.env   # set TOKENROUTER_API_KEY
aaalion ingest                # 992 text chunks + (optional A100) 100 image vectors
aaalion backend &             # http://localhost:8000
aaalion ios-sim               # iPhone 17 Pro simulator
```

Defense flow: open the simulator app, run the 9 demos in [`docs/demos/`](demos/). Each one cites the rubric item it covers.
