# Solo-Dev Plan — 陈澍枫 (Shufeng) 单人推进方案

> Reality check: 陈澍枫 (Shufeng) is functionally responsible for the entire project; teammates may or may not deliver on their modules. This plan assumes a worst-case solo execution while leaving the door open for collaboration.

## Posture

- **Default to doing it yourself.** Don't block on teammate progress. Build something that works end-to-end; integrate their work when it lands.
- **Two checkpoints**: 2026-05-25 (RAG should be running) and 2026-05-26 (backend should hit real Doubao). If either is silent, take over.
- **Keep teammates' branches usable.** Even if they don't ship, the scaffold makes it trivial for them to drop code in later.

## What's already done (status 2026-05-22, evening)

- Repo scaffold pushed to `main` + `shufeng`.
- iOS source files complete (no `.xcodeproj` yet).
- Backend serves a fixture stream + falls through to real Doubao when `DOUBAO_API_KEY` is set.
- RAG keyword-fallback retriever works.
- Mock backend (`tools/mock_backend.py`) lets iOS run offline.
- `Makefile` + `xcodegen` config.
- A100 namespace at `~/shufeng/AAALion-/`.

## What's missing — and the order to fix it

### Tier 1 — must ship by 2026-05-28 (first end-to-end demo)

1. **Generate the iOS Xcode project** and verify it builds.
   - `brew install xcodegen`
   - `make ios`
   - `open client/AAALionApp/AAALionApp.xcodeproj`
   - `Cmd+R` on iOS 17 simulator → app launches into empty chat.
2. **Get the real Doubao API key**.
   - The PDF key returns HTTP 401 (verified 2026-05-22 with `curl`). Either it's a placeholder or it's deactivated.
   - Action: ask 李雨晟 to confirm with the organizer; or post in the team WeChat to get the real one.
   - Drop into `server/.env` as `DOUBAO_API_KEY=<real>`.
3. **Bring up the backend with real Doubao**.
   - `make backend` (uvicorn) with the key in place.
   - `routes/chat.py` already routes to real Doubao when the key is set; otherwise falls back to fixture. Should "just work."
4. **Bring up Qdrant + real ingest**.
   - `cd server && docker compose up -d qdrant`.
   - Wire `rag/ingest/embed_text.py` to call the Doubao embedding endpoint (the same key works for embeddings per the PDF).
   - Wire `rag/retrieve/query.py` to `qdrant_client.search(...)` with payload filters.
   - Drop the keyword fallback for production but keep it as a `--stub` mode for offline dev.
5. **End-to-end smoke**: iPhone simulator → backend → Qdrant → Doubao → streamed response → product cards rendered.

### Tier 2 — by 2026-06-05 (bonus features)

6. **Multi-turn + negation/comparison** (bonus 4.3).
   - Backend keeps last N user/assistant turns in the prompt.
   - System prompt explicitly handles "不要 X" and "A vs B".
   - Add 30+ golden queries covering these patterns.
7. **Photo-to-product** (bonus 4.2).
   - iOS `PhotosPicker` flow, multipart POST to `/chat/multimodal`.
   - Backend image upload route; calls CLIP (deferred to A100 driver fix).
   - A100: fix driver mismatch, build CLIP image index, retrieve by image vector.
8. **Real product data**.
   - Run Perplexity prompts in `docs/DATA.md` to find a real Chinese e-commerce dataset.
   - If nothing usable found by 2026-06-01: manually curate 50 entries from Tmall/JD into the seed schema.

### Tier 3 — polish, by 2026-06-08

9. UI polish: skeleton loaders, haptic on card tap, streaming cursor animation.
10. Demo video script + recording (Screen Studio / QuickTime).
11. Slide deck for the defense.
12. README localization for judges (the README is bilingual; double-check it reads well to a Chinese reader).

## Tools you should be using

- **Trae IDE** (ByteDance's recommended AI IDE) — they recommend it; it pre-configures the Doubao model. Worth at least trying for backend + RAG work. Download: https://www.trae.ai
- **Xcode 15+** — non-negotiable for iOS. Should already be installed.
- **Claude Code / Cursor** — for AI-paired coding. Claude Code has direct repo awareness; Cursor has tighter IDE integration. Use whichever you're faster in.
- **HTTPie or curl** — for backend smoke (`http :8000/health`, `http POST :8000/chat/stream messages:='[...]'`).
- **Proxyman** ($) or **mitmproxy** (free) — inspect HTTP traffic between iOS and backend when SSE misbehaves.
- **TablePlus** — peek into Qdrant or any sqlite DB you add.
- **Screen Studio** ($) or **OBS** (free) — record the demo video.
- **Postman** — collect API examples for the team / judges.
- **GitHub CLI (`gh`)** — install with `brew install gh`, then `gh auth login`. Lets you create PRs without leaving the terminal.

## Automation you should NOT have to do manually anymore

- Regenerating `.xcodeproj` → `make ios`
- Starting backend → `make backend`
- Indexing seed → `make ingest`
- Running eval → `make eval`
- Pushing to A100 → `make sync-a100`
- Formatting → `make fmt`

If you find yourself running the same multi-step command twice this week, add it to the Makefile.

## Risk-cut checklist (review every Sunday)

- [ ] iOS demo works on a real iPhone 13, not just simulator.
- [ ] Backend survives a 30-minute live demo without crashing.
- [ ] Retrieval returns at least one relevant product on every golden query.
- [ ] No hallucinations on a 10-query manual test (price, brand, SKU).
- [ ] Real product data in `data/extra/` (or manually curated set) is in.
- [ ] Demo video recorded as backup.
- [ ] Defense slides exist.
- [ ] At least one cross-platform test (LAN with iPhone hitting MacBook backend) passes.

## When to stop adding features

After 2026-06-05, don't add anything new. Spend the last 5 days on:
- Bug fixes
- Demo polish
- Defense rehearsal (timed, with someone playing the judge)
- Documenting the system prompt / RAG choices (the judges WILL ask)

## When to ask teammates for help

- Doubao API key (李雨晟 should ask the organizer).
- Demo recording (any of you can run the camera, but I should rehearse alone first).
- Defense roleplay (better with three people).
- Final review pass on the README (a native Chinese reader other than me should sanity-check it).
