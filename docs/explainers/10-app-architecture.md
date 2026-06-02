# 10 — The whole system in one picture

## What is this?

This file is the answer to "give me the 60-second tour of how this thing
is built". If you read nothing else, read this. Everything else is
detail.

## Why does it matter?

When you point an app at a question and a product comes back, a lot of
things happen between those two events. Understanding the layers helps
you know which file to open when something breaks.

## How we built it

### The 4 layers

```
┌─────────────────────────────────────────────────────────────────┐
│  iOS APP   (Shufeng's Mac → built and pushed to iPhone 13 Pro)  │
│  Written in SwiftUI. Handles chat UI, voice, camera, cart.       │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS (Cloudflare Tunnel for now)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  BACKEND   (FastAPI on Shufeng's Mac, port 8000)                │
│  Receives chat requests, calls retrieval, calls LLM, streams     │
│  events back to iOS. Hosts cache + repurchase DB.                │
└──┬──────────────────────────────────────────────────┬───────────┘
   │                                                   │
   │ for product lookup                               │ for chat reply
   ▼                                                   ▼
┌──────────────────────────┐                ┌────────────────────────┐
│  RAG PIPELINE             │                │  LLM provider          │
│  Hybrid retrieval + CLIP  │                │  TokenRouter           │
│  + reranker + filters     │                │  → claude-haiku-4-5    │
│                           │                │  (vision-capable)      │
│  Stored in Chroma         │                │                        │
│  (~145 products indexed)  │                │  Streams reply text    │
└──────────────────────────┘                └────────────────────────┘
```

That's it. Four layers, two stores, one external service.

### Layer 1: The iOS app (SwiftUI)

Lives in `client/AAALionApp/`. The whole app is about 30 Swift files —
small by app standards. It uses:

- **SwiftUI** — Apple's modern declarative UI framework (introduced
  2019, mature by 2024). Lets us describe screens as composition of
  views rather than imperatively managing widget state.
- **`@Observable`** — Swift's newer reactive state management.
  `ChatViewModel` is `@Observable`, so any SwiftUI view that reads
  `viewModel.messages` automatically re-renders when messages change.

Key files:
- `App.swift` — entry point
- `Views/ChatView.swift` — the main chat screen
- `ViewModels/ChatViewModel.swift` — orchestrates send / receive / state
- `Services/ChatService.swift` — HTTPS + SSE parsing
- `Services/SpeechService.swift` — voice input
- `Services/CartStore.swift` — local cart state, persisted to
  UserDefaults

### Layer 2: The backend (FastAPI)

Lives in `server/app/`. About 20 Python files. Built on:

- **FastAPI** — async Python web framework. Each endpoint is an
  `async def`; the server handles many requests concurrently.
- **Pydantic** — type-safe request/response schemas. Catches malformed
  requests at the door (HTTP 422).
- **Uvicorn** — the ASGI server that actually serves FastAPI.

Key files:
- `main.py` — startup hook (`lifespan`), route registration
- `routes/chat.py` — the `/chat/stream` endpoint
- `routes/repurchase.py` — Sam's repurchase-reminders endpoints
- `routes/cache_stats.py` — observability for the cache
- `services/llm_provider.py` — multi-provider abstraction (TokenRouter,
  Anthropic, Doubao, OpenAI)
- `services/cache.py` — in-memory LRU cache
- `services/currency.py` — Frankfurter FX integration

### Layer 3: The RAG pipeline (retrieval)

Lives in `rag/`. About 15 Python files. Each query goes through:

```
query text
    │
    ├──► BM25 (keyword) ─┐
    │                    │
    ├──► BGE-zh (dense) ─┤── RRF fusion ──► top 10 candidates
    │                    │                       │
    │                                            ▼
    │                                    bge-reranker
    │                                            │
    └─────────────► CLIP (if image) ──► visual matches
                                                 │
                                                 ▼
                                            top 3-5 final
```

The retrieval orchestrator is `rag/retrieve/query.py`. Embeddings are
stored in **Chroma** — an in-process vector database that lives in
`data/.chroma/` (gitignored). Text embeddings in one collection,
image embeddings in another.

See [`02-finding-products.md`](02-finding-products.md) for the full
retrieval story.

### Layer 4: The LLM (external)

We call **claude-haiku-4-5** via a Chinese aggregator called
**TokenRouter**. TokenRouter exposes an OpenAI-compatible API and gives
us 1,000 requests per workspace for free, which is more than enough for
the demo.

The reason it's TokenRouter and not direct Anthropic: when the contest
started, the organizer-provided Doubao key was leaked publicly by
another team (on GitHub of all places). The organizer deactivated all
PDF-distributed keys. We pivoted to TokenRouter as a stable, free LLM
gateway. The provider switch is one env var change — see
`server/app/services/llm_provider.py`.

### What connects them all: HTTP and SSE

- iOS → backend: HTTPS POST to `/chat/stream`, body = JSON of the chat
  history.
- Backend → iOS: streaming HTTP response (Server-Sent Events). One JSON
  event per line. See [`07-streaming-replies.md`](07-streaming-replies.md).
- Backend → LLM provider: HTTPS to TokenRouter's API, also streamed.
- Backend → Chroma: in-process function calls (no network).

### Where everything physically lives

| Thing | Where | How to start |
|---|---|---|
| Repo | `~/Desktop/rag/AAALion-/` (Shufeng's Mac) | git clone |
| Backend (uvicorn) | Mac, port 8000 | `aaalion backend` |
| Chroma DB | `data/.chroma/` on Mac | implicit, loaded by uvicorn |
| iOS app | iPhone 13 Pro (Apple ID-signed) | `aaalion ios-device` |
| Cloudflare Tunnel | Mac (forwards 8000 to public URL) | `tools/start-tunnel.sh` |
| A100 GPU (for CLIP ingest only) | `ssh uc` | `uc-bash` |
| LLM | TokenRouter (cloud) | n/a |

### What we're planning to change

Right now the Mac is a single point of failure — if the laptop is
asleep, the iPhone can't reach anything. The Phase 2 plan is to host
the backend on a Hetzner CX22 VM (€4.5/month) so the Mac doesn't need
to be running during the defense. We'll keep the Cloudflare Tunnel as
a dev fallback.

## What this picture leaves out

- **Error handling everywhere** — retry/backoff on LLM calls, graceful
  fallback when FX is down, etc.
- **Authentication** — there isn't any. The backend is open. Fine for a
  demo; would need fixing for production.
- **Logging and metrics** — we log structured JSON per request, but
  there's no Prometheus or Grafana set up.

## Where to dig deeper

- `docs/ARCHITECTURE.md` — the engineer-facing version of this doc, with
  more detail per layer.
- `docs/PIPELINE.md` — the developer workflow (how to run + test + deploy).
- `docs/API.md` — exact JSON schemas for each endpoint.
- Every other file in this `docs/explainers/` folder — they each zoom
  into one layer.
