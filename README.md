# 狮选 LionPick

> **基于 RAG 的多模态电商智能导购 AI Agent** · _A RAG-powered multimodal e-commerce shopping agent_
>
> 团队 / Team: **AAALion** · 比赛 / Competition: ByteDance 2026 AI 全栈挑战赛
>
> 代码冻结 / Code freeze: 2026-06-10 · 答辩 / Defense: 2026-06-11 → 2026-06-19

狮选 LionPick 是一款移动端的智能导购 Agent：iOS 原生客户端 + FastAPI 流式后端 + 向量检索 + 多模态大模型。用户可以用文字或图片描述需求，Agent 基于真实商品库进行多轮对话推荐，杜绝幻觉。

LionPick is a native iOS shopping assistant. The FastAPI backend streams responses over SSE, retrieves real products from a vector index (Chroma + `bge-small-zh-v1.5`), and uses a vision-capable LLM via TokenRouter for grounded generation. It supports multi-turn dialogue, negation/exclusion, comparison, and photo-to-product search.

## Live status (2026-05-22)

| Capability | Status | Proof |
|---|---|---|
| iOS chat UI + streaming responses | ✅ | [`docs/demos/2026-05-22/01-basic-recommendation.png`](docs/demos/2026-05-22/01-basic-recommendation.png) |
| Real LLM via TokenRouter (claude-haiku-4-5) | ✅ | All demos |
| Chroma + sentence-transformers RAG (992 chunks indexed) | ✅ | All demos |
| Anti-hallucination (honest "no match") | ✅ | [`02-conditional-filter.md`](docs/demos/2026-05-22/02-conditional-filter.md) |
| Multi-turn dialogue (bonus 4.3) | 🟡 API done; UI capture pending | [`03-multi-turn.md`](docs/demos/2026-05-22/03-multi-turn.md) |
| Negation / exclusion (bonus 4.3) | ✅ | [`04-negation.md`](docs/demos/2026-05-22/04-negation.md) |
| Multi-product comparison (bonus 4.3) | ✅ | [`05-comparison.md`](docs/demos/2026-05-22/05-comparison.md) |
| Photo-to-product (bonus 4.2, vision LLM) | ✅ | [`06-photo-upload.md`](docs/demos/2026-05-22/06-photo-upload.md) |
| Simulator (iPhone 17 Pro) | ✅ | `aaalion ios-sim` |
| Physical iPhone 13 Pro deploy | ⏳ pending Team ID | [`docs/IOS_SETUP.md`](docs/IOS_SETUP.md) |
| A100 OpenCLIP indexer (bonus 4.2 depth) | ⏳ Round 3 | [`docs/HARDWARE.md`](docs/HARDWARE.md) |
| Real product data | ⏳ AI-gen seed + manual curation in progress | [`docs/research/`](docs/research/) |

---

## Team / 团队

| 中文名 | 英文名 | 角色 / Role | 模块 / Module |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | 客户端 / iOS lead · 项目兜底 / project fallback | `client/` |
| 李雨晟 | Yusheng Li | 后端 / Backend | `server/` |
| 管图杰 | Tujie Guan | 检索 / RAG | `rag/` |

> Shufeng is the project lead and fallback owner — see [`docs/SOLO_DEV_PLAN.md`](docs/SOLO_DEV_PLAN.md).

## Tech stack / 技术栈

- **客户端 / Client**: Swift 5.9, SwiftUI, iOS 17+
- **后端 / Backend**: Python 3.12, FastAPI, SSE streaming, Pydantic v2 multimodal content union
- **向量库 / Vector DB**: Chroma in-process (Qdrant supported as alternate)
- **Embeddings**: `BAAI/bge-small-zh-v1.5` (sentence-transformers, Chinese, free, CPU)
- **LLM**: `claude-haiku-4-5` via TokenRouter (75+ models behind one OpenAI-compatible API; switchable to Doubao when the new key arrives)
- **Defense fallback**: `LLM_PROVIDER=echo` for credential-less demos

## Quickstart / 快速开始

```bash
# 1. Install aaalion helper (so commands work from anywhere)
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"

# 2. Configure (copy + edit; key from https://www.tokenrouter.com/console/token)
cp .env.example server/.env
$EDITOR server/.env   # set TOKENROUTER_API_KEY

# 3. Backend + Chroma index
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
aaalion ingest                       # builds Chroma index from data/seed/ (one-time)
aaalion backend                      # uvicorn on :8000

# 4. iOS simulator (iPhone 17 Pro)
aaalion ios-sim                      # regen .xcodeproj, build, install, launch
```

For iPhone device deploy, see [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md).

## Project layout / 项目结构

```
client/    iOS 客户端 (SwiftUI)        ← 陈澍枫
server/    FastAPI 后端 (SSE, multimodal)   ← 李雨晟
rag/       Ingest / retrieve / prompts      ← 管图杰
data/      seed/ (committed) + extra/ (gitignored)
docs/      架构、流水线、政策、demos、research
meetings/  会议记录
tools/     aaalion + screenshot + check-secrets
```

## Read these next / 接下来该读这些

| Document | Purpose |
|---|---|
| [docs/DEPLOY_GUIDE.md](docs/DEPLOY_GUIDE.md) | Full step-by-step for a teammate's MacBook + iPhone ≥13 |
| [docs/demos/2026-05-22/](docs/demos/2026-05-22/) | Six end-to-end demo screenshots + verdicts |
| [docs/research/](docs/research/) | Data-availability research (no usable real dataset; manual curation plan) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | End-to-end system design |
| [docs/PIPELINE.md](docs/PIPELINE.md) | Dev SOP (develop / test / iterate) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 20-day plan |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Devices + A100 SSH rules |
| [docs/POLICY.md](docs/POLICY.md) | Team rules + commit format |
| [docs/IOS_SETUP.md](docs/IOS_SETUP.md) | Xcode signing, simulator vs device |
| [docs/API.md](docs/API.md) | Backend endpoints |
| [docs/SOLO_DEV_PLAN.md](docs/SOLO_DEV_PLAN.md) | Shufeng's solo-dev plan if teammates slip |
| [docs/FUTURE_WORK.md](docs/FUTURE_WORK.md) | Stretch ideas |
| [docs/EXECUTION_SUMMARY.md](docs/EXECUTION_SUMMARY.md) | Initial bootstrap summary |
| [docs/PLAN_2026-05-22.md](docs/PLAN_2026-05-22.md) | Latest plan (rounds 1+2) |
| [docs/commits/](docs/commits/) | Major commit records |

## License

MIT — see [LICENSE](LICENSE).
