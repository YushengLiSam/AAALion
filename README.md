<img align="right" width="140" src="client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png" alt="狮选 LionPick app icon"/>

# 狮选 LionPick

> **基于 RAG 的多模态电商智能导购 AI Agent** · _A RAG-powered multimodal e-commerce shopping agent_
>
> 团队 / Team: **AAALion** · 比赛 / Competition: ByteDance 2026 AI 全栈挑战赛
>
> 代码冻结 / Code freeze: 2026-06-10 · 答辩 / Defense: 2026-06-11 → 2026-06-19

狮选 LionPick 是一款移动端的智能导购 Agent：iOS 原生客户端 + FastAPI 流式后端 + 向量检索 + 多模态大模型。用户可以用文字、语音、相机或图片描述需求，Agent 基于真实商品库进行多轮对话推荐，杜绝幻觉。

LionPick is a native iOS shopping assistant. The FastAPI backend streams responses over SSE, retrieves real products from a vector index (Chroma + `bge-small-zh-v1.5` + OpenCLIP ViT-B/32 on A100), and uses a vision-capable LLM via TokenRouter for grounded generation. Multi-turn dialogue, negation/exclusion, comparison, photo-to-product search, voice input, TTS playback.

<br clear="all"/>

## Live status (2026-05-23, Round 3 shipped)

| Capability | Status | Proof |
|---|---|---|
| iOS chat UI + streaming responses | ✅ | [`docs/demos/2026-05-23/01-basic-themed.png`](docs/demos/2026-05-23/01-basic-themed.png) |
| Real LLM via TokenRouter (claude-haiku-4-5) | ✅ | All demos |
| Chroma + sentence-transformers RAG (992 chunks) | ✅ | All demos |
| Anti-hallucination (honest "no match") | ✅ | [`02-conditional-filter.md`](docs/demos/2026-05-22/02-conditional-filter.md) |
| Multi-turn dialogue (4.3 ⭐) | ✅ API + edit-message UX | [`03-multi-turn.md`](docs/demos/2026-05-22/03-multi-turn.md) |
| Negation / exclusion (4.3 ⭐⭐) | ✅ | [`04-negation.md`](docs/demos/2026-05-22/04-negation.md) |
| Multi-product comparison (4.3 ⭐⭐⭐) | ✅ | [`05-comparison.md`](docs/demos/2026-05-22/05-comparison.md) |
| Photo-to-product via vision LLM (4.2 ⭐⭐⭐) | ✅ | [`06-photo-clip.png`](docs/demos/2026-05-23/06-photo-clip.png) |
| **OpenCLIP image retrieval on A100 (4.2 depth)** | ✅ | 100 images indexed; image-first retrieval in backend |
| **Voice input (4.2 ⭐)** | ✅ | Speech.framework, mic button, zh-CN |
| **TTS playback (4.2 ⭐⭐)** | ✅ | AVSpeechSynthesizer, long-press → Speak |
| **Settings screen (runtime backend URL)** | ✅ | gear icon → URL + Test Connection |
| **Edit / Copy / Speak context menu** | ✅ | long-press any message |
| **Camera + Files attachment** | ✅ | `+` menu → Photos / Camera / Files |
| **New theme + app icon** | ✅ | Claude-designed palette, TokenRouter-generated lion icon |
| Simulator (iPhone 17 Pro) | ✅ | `aaalion ios-sim` |
| Physical iPhone 13 Pro deploy | ✅ | weekly `aaalion resign` (free-tier 7-day cert) |
| Hot-query cache | 🟡 file ready; integration deferred | `server/app/services/cache.py` |
| Real product data | ⏳ AI-gen seed + manual curation in progress | [`docs/research/`](docs/research/) |
| Shopping cart / ordering (4.1) | ⏳ out of scope v1 | per [`docs/POLICY.md`](docs/POLICY.md) |

> 📋 **Next iteration up for team review**: [`docs/PROPOSAL_2026-05-24.md`](docs/PROPOSAL_2026-05-24.md). Sam / Tujie please comment before solo execution kicks back in.

---

## Team / 团队

| 中文名 | 英文名 | 角色 / Role | 模块 / Module |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | 客户端 / iOS lead · 项目兜底 / project fallback | `client/` |
| 李雨晟 | Yusheng Li | 后端 / Backend | `server/` |
| 管图杰 | Tujie Guan | 检索 / RAG | `rag/` |

> Shufeng is the project lead and fallback owner — see [`docs/SOLO_DEV_PLAN.md`](docs/SOLO_DEV_PLAN.md).

## Tech stack / 技术栈

- **客户端 / Client**: Swift 5.9, SwiftUI, iOS 17+. Speech.framework + AVSpeechSynthesizer + PhotosPicker + UIImagePickerController + .fileImporter.
- **后端 / Backend**: Python 3.12, FastAPI, SSE, Pydantic v2 multimodal content union.
- **向量库 / Vector DB**: Chroma in-process. Two collections: `products_text` (992 chunks via `BAAI/bge-small-zh-v1.5`) + `products_image` (100 vectors via OpenCLIP ViT-B/32 on A100).
- **LLM**: `claude-haiku-4-5` (vision-capable) via TokenRouter. Swappable to Doubao, OpenAI, Anthropic, or local echo via `LLM_PROVIDER` env.
- **Design tokens**: Claude-designed warm-ivory + amber-gold + deep-espresso palette (see [`client/AAALionApp/design-tokens.json`](client/AAALionApp/design-tokens.json)).

## Quickstart / 快速开始

```bash
# 1. Install aaalion helper (works from anywhere)
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"

# 2. Configure (key from https://www.tokenrouter.com/console/token)
cp .env.example server/.env
$EDITOR server/.env   # set TOKENROUTER_API_KEY

# 3. Backend + Chroma text index
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
aaalion ingest                       # 992 text chunks (one time, ~90 sec)
aaalion backend                      # uvicorn on 0.0.0.0:8000

# 4. iOS simulator
aaalion ios-sim                      # regen .xcodeproj, build, install, launch
```

Backend URL is hardcoded in `client/AAALionApp/AAALionApp/Config.swift` (`defaultBackendURL`). **You can also change it at runtime via the in-app Settings (gear icon)** — no rebuild needed for LAN IP changes.

For iPhone device deploy, see [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md). For the A100 CLIP image index, see [`docs/IMPLEMENTATION_GUIDE.md`](docs/IMPLEMENTATION_GUIDE.md).

## Project layout / 项目结构

```
client/    iOS 客户端 (SwiftUI, Speech, AVFoundation)  ← 陈澍枫
server/    FastAPI 后端 (SSE, multimodal, cache)        ← 李雨晟
rag/       Ingest / retrieve / prompts / eval / CLIP    ← 管图杰
data/      seed/ (committed) + .chroma/ (gitignored)
docs/      架构、流水线、政策、demos、research、proposals
meetings/  会议记录
tools/     aaalion + screenshot + check-secrets
```

## Read these next / 接下来该读这些

| Document | Purpose |
|---|---|
| ⭐ [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md) | Single-page index — start here if new to the repo |
| 📋 [docs/PROPOSAL_2026-05-24.md](docs/PROPOSAL_2026-05-24.md) | Next-iteration proposal (awaiting team review) |
| [docs/RUBRIC_MAPPING.md](docs/RUBRIC_MAPPING.md) | PDF §4 → code/artifact mapping for defense |
| [docs/DEPLOY_GUIDE.md](docs/DEPLOY_GUIDE.md) | Step-by-step for a teammate's MacBook + iPhone ≥13 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | All gotchas + fixes |
| [docs/demos/](docs/demos/) | All recorded demo screenshots + verdicts |
| [docs/research/](docs/research/) | Data-availability research |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | End-to-end design |
| [docs/PIPELINE.md](docs/PIPELINE.md) | Dev SOP |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 20-day plan |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Devices + A100 SSH rules |
| [docs/POLICY.md](docs/POLICY.md) | Team rules + commit format |
| [docs/IOS_SETUP.md](docs/IOS_SETUP.md) | Xcode, signing, weekly resign cadence |
| [docs/API.md](docs/API.md) | Backend endpoints |
| [docs/SOLO_DEV_PLAN.md](docs/SOLO_DEV_PLAN.md) | Fallback execution plan |
| [docs/FUTURE_WORK.md](docs/FUTURE_WORK.md) | Stretch ideas |
| [docs/EXECUTION_SUMMARY.md](docs/EXECUTION_SUMMARY.md) | Initial bootstrap summary |
| [docs/commits/](docs/commits/) | Major-commit records |

## License

MIT — see [LICENSE](LICENSE).
