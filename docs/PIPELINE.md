# Development Pipeline (Team SOP)

Read this before touching any code. It tells you the order of operations, how to test what you're building, and how to land changes without breaking the other two developers.

## How to develop

### Parallel-work-friendly order

1. **管图杰 / RAG** brings up the vector index + ingests `data/seed/` first. Output: a populated Chroma collection (or Qdrant `:6333`).
2. **李雨晟 / backend** wraps it with FastAPI, exposing `/chat/stream`. He can stub retrieval initially by returning a fixed top-3 from a local JSON.
3. **陈澍枫 / iOS** builds the chat UI against the SSE endpoint. He can stub the backend by running `python tools/mock_backend.py` in another terminal.

### Stubs each role provides for the others

- **RAG stub**: `rag/retrieve/query.py` accepts `--stub` and returns the first 3 products from `data/seed/`. No Qdrant required.
- **Backend stub**: serve fixture tokens (read from `server/app/fixtures/sample_stream.txt`) so iOS can test SSE parsing without Doubao.
- **iOS stub**: not needed by others; iOS only consumes.

### Branch model

- `main` is stable. Only scaffold + accepted PRs land here.
- Each developer's personal branch: `shufeng`, `sam`, `tujie`. Work-in-progress lives here.
- Feature branches when you want a clean PR: `<owner>/<feature>` e.g. `tujie/negation-filter`.
- Rebase your personal branch onto `main` daily so divergence stays small.

### Commit hygiene

- Imperative subject ("Add SSE delta type", not "Added"/"Adding").
- Body: *why*, not *what*.
- One logical change per commit; small commits land faster than perfect ones.

### PR rules

- PR title: same form as commit subject.
- Description must include:
  - **What** changed (1-2 sentences).
  - **Why** (link to the requirement or issue).
  - **How to test** (commands + expected output).
- Reviewer:
  - iOS PRs → 陈澍枫 self-merges (no other iOS dev).
  - Backend → 李雨晟 self-merges.
  - RAG → 管图杰 self-merges.
  - **Cross-area** PRs (e.g. changing an API contract) → require the affected owner's approval.

## How to test

Three layers, in increasing cost:

### 1. RAG eval (cheapest, run on every RAG change)

```bash
cd rag
python -m eval --golden eval/golden.jsonl
```

Reports recall@5 and a CSV of misses. Target: ≥ 80% recall@5 on the golden set by 06-05.

### 2. Backend integration

```bash
cd server
pytest tests/
```

Uses a live Qdrant (started by the test fixture) and a **mocked Doubao client** — no real API calls in tests, deterministic. Cost: 0¥.

### 3. iOS

- `XCTest` for `ChatService` SSE parsing (`Cmd+U` in Xcode).
- Manual UI smoke on iPhone 13 simulator + the real device (for camera flow).
- Before each demo: run the full end-to-end on the real iPhone 13 with the real backend.

## How to iterate

### Daily cadence

- Morning: pull `main`, rebase your branch.
- Stand-up text in the team channel: yesterday / today / blockers (one line each).
- Evening: push your branch even if WIP — protects against laptop loss and lets others see progress.

### Weekly cadence (every Sunday)

- 30-min sync. Demo your latest. Update [`ROADMAP.md`](ROADMAP.md). Re-prioritize bonus features based on what's risky.

### When you're stuck

- Stuck for >30 min: ask in the team channel with a snippet + what you tried.
- Stuck for >2 hours: pair-debug on a call. Time is more expensive than ego.

### Code review tips for this team

- Trust the owner's call on their area; review for correctness, not style preference.
- Approve if the diff makes the system better, even if you'd write it differently.
- Never block on hypothetical future use cases — the freeze is 2026-06-10.

## Local-run quickstart (5 commands)

```bash
cd server && docker compose up -d qdrant         # 1. vector DB
cd ../rag && python -m ingest.run                # 2. one-time index
cd ../server && uvicorn app.main:app --reload    # 3. backend
open ../client/AAALionApp/AAALionApp.xcodeproj   # 4. iOS — then Cmd+R
python ../tools/screenshot_watcher.py            # 5. (optional) for design loop
```
