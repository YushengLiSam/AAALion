# AAALion-

**Project**: 基于 RAG 的多模态电商智能导购 AI Agent
**Competition**: ByteDance 2026 AI 全栈挑战赛
**Code-freeze**: 2026-06-10 · **Defense**: 2026-06-11 → 2026-06-19

A multimodal Retrieval-Augmented Generation (RAG) agent for Chinese e-commerce: native iOS chat client, FastAPI backend with streaming responses, vector retrieval over real product data, and Doubao-Seed-2.0-lite for generation.

---

## Team

| Member | Role | Owns |
|---|---|---|
| Shufeng Chen (小淫猫) | iOS client | `client/` |
| Yusheng Li (Sam) | Backend | `server/` |
| Tujie Guan | RAG | `rag/` |

## Tech stack

- **Client**: Swift 5.9, SwiftUI, iOS 17+
- **Backend**: Python 3.11, FastAPI, SSE streaming
- **Vector DB**: Qdrant (default) · Chroma documented as fallback
- **Embeddings**: Doubao-embedding-vision (text) + OpenCLIP ViT-B/32 (images, indexed on A100)
- **LLM**: Doubao-Seed-2.0-lite via `https://ark.cn-beijing.volces.com/api/v3/`

## Quickstart (5 commands)

```bash
# 1. Vector DB
cd server && docker compose up -d qdrant

# 2. Build the index (one time)
cd ../rag && pip install -r requirements.txt && python -m ingest.run

# 3. Run the backend
cd ../server && pip install -r requirements.txt && uvicorn app.main:app --reload

# 4. Open the iOS app
open ../client/AAALionApp/AAALionApp.xcodeproj  # then Cmd+R

# 5. (Optional) Screenshot watcher for the design loop
python ../tools/screenshot_watcher.py
```

**Doubao key**: copy `.env.example` to `.env` (in `server/`) and paste the key shared in the team channel. The key NEVER reaches the iOS client.

## Project layout

```
client/    iOS app (SwiftUI)
server/    FastAPI backend (SSE, Doubao orchestration)
rag/       Ingest / retrieve / prompts / eval
data/      seed/ (committed) + extra/ (gitignored)
docs/      Architecture, pipeline, hardware, policy, roadmap, future work
meetings/  YYYY-MM-DD-topic.md
tools/     screenshot_watcher.py + helpers
```

## Read these next

| Document | Why |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | End-to-end system design |
| [docs/PIPELINE.md](docs/PIPELINE.md) | How to develop / test / iterate (team SOP) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 20-day day-by-day plan with owners |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Devices, OS, A100 SSH-UC role |
| [docs/POLICY.md](docs/POLICY.md) | Persistent team preferences and rules |
| [docs/DATA.md](docs/DATA.md) | Where to find more real data (the bundled set is AI-generated) |
| [docs/API.md](docs/API.md) | Backend endpoints |
| [docs/FUTURE_WORK.md](docs/FUTURE_WORK.md) | Stretch ideas per person |

## License

MIT — see [LICENSE](LICENSE).
