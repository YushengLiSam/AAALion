# 狮选 LionPick

> **基于 RAG 的多模态电商智能导购 AI Agent** · _A RAG-powered multimodal e-commerce shopping agent_
>
> 团队 / Team: **AAALion** · 比赛 / Competition: ByteDance 2026 AI 全栈挑战赛
>
> 代码冻结 / Code freeze: 2026-06-10 · 答辩 / Defense: 2026-06-11 → 2026-06-19

狮选 LionPick 是一款移动端的智能导购 Agent：iOS 原生客户端 + FastAPI 流式后端 + 向量检索 + 豆包 (Doubao-Seed-2.0-lite) 生成。用户可以用文字或图片描述需求，Agent 基于真实商品库进行多轮对话推荐，杜绝幻觉。

LionPick is a native iOS shopping assistant. The FastAPI backend streams responses over SSE, retrieves real products from a vector index, and uses Doubao-Seed-2.0-lite for grounded generation. It supports multi-turn dialogue, negation/exclusion, comparison, and a photo-to-product flow.

---

## Team / 团队

| 中文名 | 英文名 | 角色 / Role | 模块 / Module |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | 客户端 / iOS lead · 项目兜底 / project fallback | `client/` |
| 李雨晟 | Yusheng Li | 后端 / Backend | `server/` |
| 管图杰 | Tujie Guan | 检索 / RAG | `rag/` |

> 陈澍枫 同时兜底其他模块 — 如有进度风险，按 [SOLO_DEV_PLAN.md](docs/SOLO_DEV_PLAN.md) 推进。
>
> Shufeng is the project lead and fallback owner — see [docs/SOLO_DEV_PLAN.md](docs/SOLO_DEV_PLAN.md).

## Tech stack / 技术栈

- **客户端 / Client**: Swift 5.9, SwiftUI, iOS 17+
- **后端 / Backend**: Python 3.11, FastAPI, SSE streaming
- **向量库 / Vector DB**: Qdrant (default) · Chroma (fallback)
- **Embeddings**: Doubao-embedding-vision (text) + OpenCLIP ViT-B/32 (images, indexed on A100)
- **LLM**: Doubao-Seed-2.0-lite via `https://ark.cn-beijing.volces.com/api/v3/`

## Quickstart / 快速开始

```bash
# 0. (一次) 配置环境变量 / set env once
cp .env.example server/.env
# fill DOUBAO_API_KEY in server/.env (从微信群获取真实 Key)

# 1. 向量库 / vector DB
cd server && docker compose up -d qdrant

# 2. 一次性建索引 / one-time index
cd ../rag && pip install -r requirements.txt && python -m ingest.run

# 3. 后端 / backend
cd ../server && pip install -r requirements.txt && uvicorn app.main:app --reload

# 4. iOS 工程 / iOS project — 需要 xcodegen
cd ../client/AAALionApp && xcodegen && open AAALionApp.xcodeproj   # then Cmd+R

# 5. (可选) 截屏助手 / screenshot helper
python ../tools/screenshot_watcher.py
```

或者用 `make`:

```bash
make help           # 列出所有命令
make backend        # 起后端
make ingest         # 建索引
make ios            # 重新生成 .xcodeproj
make sync-a100      # rsync 到 A100
```

## Project layout / 项目结构

```
client/    iOS 客户端 (SwiftUI)             ← 陈澍枫
server/    FastAPI 后端 (SSE, Doubao 编排)   ← 李雨晟
rag/       Ingest / retrieve / prompts      ← 管图杰
data/      seed/ (committed) + extra/ (gitignored)
docs/      架构、流水线、硬件、政策、路线图等
meetings/  会议记录 / YYYY-MM-DD-topic.md
tools/     screenshot_watcher.py + 辅助脚本
```

## Read these next / 接下来该读这些

| Document | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 端到端系统设计 / system design |
| [docs/PIPELINE.md](docs/PIPELINE.md) | 开发流程 / dev SOP |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 20 天每日计划 / day-by-day plan |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 设备 / devices, A100 SSH rules |
| [docs/POLICY.md](docs/POLICY.md) | 团队规则 / team policy |
| [docs/DATA.md](docs/DATA.md) | 如何找真实数据 / how to source real data |
| [docs/API.md](docs/API.md) | 后端接口 / backend endpoints |
| [docs/SOLO_DEV_PLAN.md](docs/SOLO_DEV_PLAN.md) | 陈澍枫单人推进计划 / Shufeng's solo-dev plan |
| [docs/FUTURE_WORK.md](docs/FUTURE_WORK.md) | 加分项 / stretch ideas |
| [docs/EXECUTION_SUMMARY.md](docs/EXECUTION_SUMMARY.md) | 仓库初始化总结 / bootstrap summary |
| [docs/commits/](docs/commits/) | 重大提交记录 / major commit records |

## License

MIT — see [LICENSE](LICENSE).
