# Execution Summary — Repo Bootstrap (2026-05-22)

> 产品名 / Product: **狮选 LionPick** · 团队名 / Team: **AAALion**
>
> **Audience / 受众**: 李雨晟 and 管图杰. 陈澍枫 did the initial bootstrap; this is what was done and what's open for you.

## TL;DR

The repo is scaffolded, pushed to `main` and `shufeng` on `https://github.com/YushengLiSam/AAALion-`, mirrored to the A100 under `~/shufeng/AAALion-/`, and the screenshot tool works. **Nothing is functional end-to-end yet** — the chat endpoint returns a fixture and retrieval is a keyword fallback. Both of those are deliberately stubbed so you can land your real implementations independently.

## What was done

### 1. Repo skeleton

Top-level layout (see `README.md`):
```
client/  server/  rag/  data/  docs/  meetings/  tools/  screenshots/  .github/
```

Each area has a dedicated README explaining how to bring it up and what's stubbed. `.gitignore` covers Python/Swift/macOS/secrets and the gitignored `data/extra/` for real data, `screenshots/` for the design loop, and `docs/POLICY_LOCAL.md` for private policy entries.

### 2. iOS client (Shufeng)

A SwiftUI scaffold targeting iOS 17. Lives in `client/AAALionApp/AAALionApp/`. Source-only — no `.xcodeproj` yet (Xcode generates that locally, per `client/AAALionApp/README.md`).

Files in place:
- `AAALionAppApp.swift` (entrypoint), `Config.swift` (backend URL).
- `Models/`: `Message`, `ProductCard`, `ChatDelta` (Codable).
- `Services/ChatService.swift` — hand-rolled SSE on `URLSession.bytes(for:)`, parses `data: …\n\n` framed events.
- `ViewModels/ChatViewModel.swift` — `@Observable`, tracks streaming state, dispatches deltas/product cards to the message bubble.
- `Views/`: `ChatView`, `MessageBubbleView`, `ProductCardView`, `ProductDetailView`. Streaming text + horizontal product cards + tap-to-detail navigation.

### 3. Backend (Sam's area, but pre-stubbed)

In `server/`. FastAPI app with three routes:
- `GET /health` — returns 200.
- `POST /chat/stream` — streams a **fixture** (`好的，为你推荐这款洁面产品。` + one placeholder product card + `done`). Replace with real Doubao + RAG.
- `GET /products/{id}` — returns the indexed JSON for any seed product.

`services/doubao_client.py` and `services/rag_client.py` are stubs with clear `TODO(sam)` / `TODO(tujie)` markers. `Dockerfile` + `docker-compose.yml` bring up Qdrant + the backend for private deployment.

### 4. RAG (Tujie's area, pre-stubbed)

In `rag/`. The chunker is real (`ingest/chunk.py` turns each product JSON into multiple `desc`/`faq`/`review` chunks with metadata). The text embedder and image embedder are stubs returning zero-vectors / empty lists, with `TODO(tujie)` comments pointing at the exact next steps.

`retrieve/query.py` is a **keyword-overlap fallback** that works against the seed JSONs without Qdrant — enough for Sam to wire the orchestration. Swap it for `qdrant_client.search(...)` later.

`prompts/system.md` is the anti-hallucination system prompt template. `eval/golden.jsonl` has 10 seed cases; grow to 30+ by 2026-06-05. `eval/run.py` reports recall@5.

### 5. Data

`data/seed/` contains the unzipped 100-product dataset (committed, 9.4 MB). **Important**: it's AI-generated — see `docs/DATA.md` for how to source real data. `data/extra/` is gitignored for real datasets.

### 6. Tooling

- `tools/screenshot_watcher.py` — polls macOS `NSPasteboard` every 250ms; when `changeCount` increments AND the pasteboard carries image data with NO file URL, saves a PNG to `screenshots/`. Smoke-tested (`./.venv/bin/python tools/screenshot_watcher.py` starts cleanly and stops on Ctrl+C). Manual run only.
- `tools/ssh_a100.sh` — drops you into `~/shufeng/AAALion-/` on `uc`.

### 7. A100 setup

- Created `~/shufeng/AAALion-/` (sibling of the existing `~/shufeng/cuda-fuzzing/`, which was NOT touched — verified by mtime).
- Mirrored the project there via `rsync` (the A100 has no GitHub credentials).
- `nvidia-smi` reports a "Driver/library version mismatch" — flagged in `docs/HARDWARE.md`. Not blocking until we run CLIP, but Tujie should reconcile before the photo-search push.

### 8. Documentation pass

In `docs/`:
- `ARCHITECTURE.md` — end-to-end design, including the multimodal photo-search path.
- `PIPELINE.md` — team SOP: develop / test / iterate.
- `HARDWARE.md` — devices, A100 rules.
- `POLICY.md` — shared persistent rules (the "policy" file Shufeng wanted). Private entries go to gitignored `POLICY_LOCAL.md`.
- `ROADMAP.md` — 20-day plan with owners.
- `DATA.md` — where to find real data (Perplexity / Gemini prompts).
- `API.md` — endpoint reference.
- `FUTURE_WORK.md` — stretch ideas per person.
- `meeting_template.md` — used by `meetings/`.

`meetings/2026-05-20-kickoff.md` imports the kickoff meeting summary and records our team's post-kickoff decisions.

## Open work, per person

### 李雨晟 (backend)

1. Replace `server/app/services/doubao_client.py` `NotImplementedError` with a real `openai.AsyncOpenAI` streaming call against the ARK endpoint.
2. Replace `routes/chat.py:_fake_stream` with real orchestration:
   - Pull last user message → call `services.rag_client.stub_top_k(...)` (or Tujie's real retriever) → assemble prompt from `rag/prompts/system.md` → stream Doubao output.
   - Emit `product_card` events for each cited product, sourced from the indexed JSON (never invented).
3. Implement `POST /chat/multimodal` for image upload (multipart, 5 MB cap).
4. Add `pytest` tests for the SSE event framing.

### 管图杰 (RAG)

1. Wire `rag/ingest/embed_text.py` to the real Doubao embedding endpoint.
2. Wire `rag/ingest/embed_image.py` to OpenCLIP ViT-B/32; run on `uc` once driver mismatch is fixed.
3. Upsert into Qdrant in `rag/ingest/run.py` — two collections per `rag/README.md` schema.
4. Replace `rag/retrieve/query.py` with real Qdrant search + payload filters (category, brand-exclude, price range).
5. Grow `rag/eval/golden.jsonl` to 30+ cases with real expected ids; rerun `python -m rag.eval.run` and report recall@5.

### 陈澍枫 (iOS)

1. Generate the `.xcodeproj` in Xcode following `client/AAALionApp/README.md`. Commit the project file.
2. Wire `ChatService` to a real backend; verify SSE parsing with `XCTest`.
3. Implement `PhotosPicker` flow for `POST /chat/multimodal` (image upload).
4. Polish bubbles, streaming animation, product card haptics.
5. Demo video script.

### Joint (any of us)

1. Source real product data (see `docs/DATA.md`). Hard fallback: 50 entries hand-curated by 2026-06-01.
2. Run end-to-end smoke on 2026-05-28 — gate to confirm the architecture holds before pushing on bonus features.
3. Record demo video by 2026-06-08.

## How to pick this up cold

```bash
git clone https://github.com/YushengLiSam/AAALion-.git
cd AAALion-
cat README.md docs/PIPELINE.md docs/ARCHITECTURE.md docs/ROADMAP.md
# Then jump to your area's README: server/README.md or rag/README.md or client/AAALionApp/README.md
```

If anything in this summary is out of date, update it. The doc lives at `docs/EXECUTION_SUMMARY.md` and should be a snapshot of what's been built — bump it when you land a major area.
