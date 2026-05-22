# chore(repo): initial scaffold (client/server/rag/docs/meetings/data/tools)

**Date**: 2026-05-22
**SHA**: `235224f`
**Author**: 陈澍枫 (Shufeng)

## Why

We had no codebase. The competition deadline is 2026-06-10 and we need a runnable scaffold all three teammates can build into in parallel. Without firm structure each developer makes incompatible choices and integration cost grows fast.

## What changed

- Created the full project layout: `client/ server/ rag/ data/ docs/ meetings/ tools/ screenshots/ .github/`.
- iOS scaffold under `client/AAALionApp/AAALionApp/`: `AAALionAppApp.swift`, `Config.swift`, models (Message, ProductCard, ChatDelta), services (ChatService SSE + ProductService), ViewModels (ChatViewModel @Observable), Views (ChatView, MessageBubbleView, ProductCardView, ProductDetailView).
- Backend (FastAPI) under `server/app/`: main, config, routes (health, chat, products), schemas, services (Doubao stub, RAG stub). `requirements.txt`, `Dockerfile`, `docker-compose.yml`.
- RAG package under `rag/`: chunk (real), embed_text + embed_image (stubs), retrieve/query (keyword fallback), rerank (identity), prompts/system.md, eval/golden.jsonl (10 cases), eval/run.py.
- Seed data: unzipped `ecommerce_agent_dataset_供参考.zip` into `data/seed/` (4 categories, 100 products, ~9.4 MB).
- Tools: `screenshot_watcher.py` (NSPasteboard polling), `ssh_a100.sh`.
- Docs: ARCHITECTURE, PIPELINE, HARDWARE, POLICY, ROADMAP, DATA, API, FUTURE_WORK, meeting_template.
- Meetings: `2026-05-20-kickoff.md`.
- Root: README, LICENSE (MIT), `.gitignore`, `.env.example`, PR template.

## Procedure

```
mkdir -p AAALion-/{client/AAALionApp/AAALionApp/{Models,Services,ViewModels,Views,Assets.xcassets},server/app/{routes,services,schemas},rag/{ingest,retrieve,prompts,eval},data/{seed,extra},docs,meetings,screenshots,tools,.github}
unzip ecommerce_agent_dataset_供参考.zip -d data/seed/
# (wrote all source files)
git init -q && git branch -M main && git add . && git commit -m "Initial scaffold: client/server/rag/docs/meetings/data/tools"
git remote add origin https://github.com/YushengLiSam/AAALion-.git
git push -u origin main
git checkout -b shufeng && git push -u origin shufeng
```

## Outcome / Verification

- `find AAALion- -type f | wc -l` → 266 files.
- `git push` to both `main` and `shufeng` succeeded.
- Screenshot watcher smoke-tested: started, watched correct dir, stopped cleanly on SIGINT.
- A100 namespace created at `~/shufeng/AAALion-/` via rsync; `~/shufeng/cuda-fuzzing/` mtime confirmed unchanged.
- Doubao API key from the PDF returns HTTP 401 — flagged as a known issue.

## Follow-ups

- Generate `.xcodeproj` (deferred — needs xcodegen install + Xcode).
- Resolve A100 `nvidia-smi` driver mismatch before running CLIP.
- Replace AI-generated seed data with real product data (see `docs/DATA.md`).
- 李雨晟 / 管图杰 to pick up `server/` and `rag/` respectively per `docs/SOLO_DEV_PLAN.md` and area READMEs.
