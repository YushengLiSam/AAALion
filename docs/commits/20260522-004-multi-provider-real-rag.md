# feat(server,rag): multi-provider LLM + real Chroma RAG, end-to-end working

**Date**: 2026-05-22 (late)
**SHA**: (fill in after commit)
**Author**: 陈澍枫

## Why

Three reasons converged:

1. **API key leak** — organizer (Shida Wang) announced the PDF Doubao key was leaked publicly, abused by non-participants, and **deactivated**. We can't depend on Doubao until new keys arrive.
2. **Solo posture** — teammates haven't responded to the WeChat update; assume iOS / backend / RAG are all on me, so the system needs to *work* end-to-end today, not block on anyone.
3. **iOS dev needs a real backend** — fixtures only get you so far. To verify SSE framing, product cards, and streaming UX, the backend has to actually retrieve and generate. The user is also building the iOS app and shouldn't be blocked by a half-built backend.

## What changed

### LLM provider layer
- `server/app/services/llm_provider.py` — new abstraction with 4 implementations:
  - `AnthropicProvider` (default when `ANTHROPIC_API_KEY` is set)
  - `OpenAICompatibleProvider` (used for Doubao when key arrives, and for OpenAI)
  - `EchoProvider` (deterministic, no-network — guaranteed working fallback)
  - Factory: picks based on `LLM_PROVIDER` env var; auto-detects when unset.
- `server/app/routes/chat.py` rewritten to use the provider abstraction; gracefully degrades to echo when no key.
- `server/app/services/doubao_client.py` left in place but unused (kept for reference); marked deprecated by chat.py.

### Real RAG pipeline
- `rag/store.py` — new Chroma-in-process store (persists at `data/.chroma/`). Qdrant remains supported but Chroma is the dev default for solo simplicity.
- `rag/ingest/embed_text.py` — sentence-transformers (`BAAI/bge-small-zh-v1.5`) embedder. Free, ~30 MB model, runs on CPU. No Doubao dependency.
- `rag/ingest/run.py` — chunks the seed (992 chunks from 100 products), embeds, upserts into Chroma.
- `rag/retrieve/query.py` — real semantic search with metadata filters (category, brand-exclude, price range). Keyword fallback retained for offline cases.
- `server/app/services/rag_client.py` — fixed `parents[]` index (was `parents[2]` pointing at `server/`, now `parents[3]` pointing at the repo root).

### Configuration
- `.env.example` — added `LLM_PROVIDER`, ANTHROPIC_/DOUBAO_/OPENAI_ blocks. Default `anthropic`.
- `server/requirements.txt` — added `anthropic`, `chromadb`, `sentence-transformers`.

### Secret-leak prevention
- `tools/check-secrets.sh` — scans staged files for ARK/Anthropic/OpenAI key shapes. Caught and forced redaction of the now-dead Doubao key from `WECHAT_UPDATE_2026-05-22.md` and commit record 003.
- `Makefile` — `make check-secrets` target.

### Run-from-anywhere helper
- `tools/aaalion` — bash script that finds the repo (walks up from PWD, then checks common candidate paths) and `exec make`s the requested target. Works from `$HOME`, `~/Documents`, anywhere.
- `Makefile install-cli` — symlinks `tools/aaalion` into `/usr/local/bin/`.

### Documentation pass
- `docs/IOS_SETUP.md` — full state of iOS tooling on Shufeng's Mac (Xcode missing, Command Line Tools only). Steps to install Xcode, then `aaalion ios` to one-shot a runnable simulator app. Honest answer for "Claude Code Mobile" (no such product) and "openclaw" (interpreting as OpenCLIP).
- `docs/HONEST_ANSWERS.md` — running scratch for "be honest" questions; confidence levels and things I genuinely don't know.
- `docs/POLICY.md` — added "2026-05-22: Doubao key leak incident" section under Secrets.
- `README.md` + others — stripped `(Sam)`, `(Tujie)`, `小淫猫` nicknames.

### Memory
- `feedback-commit-format`, `feedback-major-commit-records`, `feedback-solo-posture` — new memory entries from earlier today.
- `reference-doubao-key` — updated with the leak details.
- `user-profile` — recorded nickname change (小淫猫 → 圆头小狮子) with explicit "do not use in committed docs" rule.

## Procedure

```
# Detect environment
ls -d /Applications/Xcode*.app                # → no Xcode
env | grep ANTHROPIC                          # → ANTHROPIC_API_KEY set but EMPTY
python3.12 --version                          # → 3.12.13 (good; 3.14 broke pydantic-core)

# Rebuild venv on 3.12 (3.14 lacks prebuilt wheels for pydantic-core)
rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt

# Real ingest
CHROMA_TELEMETRY=False python -m rag.ingest.run
# → chunks: 992 | upserted; collection now has 992 docs

# Real semantic retrieval smoke
python -c "from rag.retrieve.query import query; print(query('适合油皮的洗面奶', k=3))"
# → semantically correct top hits

# Start backend with auto-provider fallback
cd server && LLM_PROVIDER=anthropic uvicorn app.main:app --port 8765 &
curl -sN -X POST http://127.0.0.1:8765/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"推荐一款适合油皮的洗面奶"}]}'
# → first attempt: HTTP 500 → fixed sys.path off-by-one in rag_client.py → retry
# → second attempt: SSE stream of delta events + 3 product_card events + done
```

## Outcome / Verification

- ✅ Health endpoint returns `{"status":"ok","version":"0.1.0"}`.
- ✅ `/chat/stream` emits valid SSE: deltas → 3 product cards → done.
- ✅ Product cards contain real product_id / title / brand / base_price / image_url from `data/seed/`.
- ✅ Semantic retrieval is much better than the keyword fallback (e.g. "适合油皮的洗面奶" → top 3 are all skincare products, with the right cleanser at rank 1 by ~0.72 cosine).
- ✅ Echo provider works without any API key — guarantees demos never 500 due to missing creds.
- ✅ `swift -frontend -parse` on all iOS sources → no errors.
- ✅ `tools/check-secrets.sh` clean after redaction.
- ✅ Backend gracefully falls back: anthropic → echo when key is empty.

## Follow-ups

- **Install Xcode** — biggest blocker for actually running the iOS app. ~10 GB download.
- **Get a real LLM key**: either wait for new Doubao OR get an Anthropic key from console.anthropic.com OR install ollama locally for a free dev loop.
- **Wire OpenCLIP** on the A100 once nvidia driver mismatch is fixed (for 拍照找货 bonus 4.2).
- **Grow `rag/eval/golden.jsonl`** beyond 10 cases — need 30+ with real expected ids before 2026-06-05.
- **Real product data** — bundled set is AI-generated; manual curation of 50 entries is the recommended floor by 2026-06-01.
- **Pre-commit hook** wiring `tools/check-secrets.sh` so leaks can't even reach `git push`.
