# Architecture

End-to-end design of the RAG-based multimodal e-commerce agent.

## High-level flow

```
 ┌──────────────┐   text+image   ┌─────────────┐   query    ┌──────────┐
 │ iOS App      │ ─────────────► │  FastAPI    │ ─────────► │ Chroma   │
 │ (SwiftUI)    │                │  /chat      │            │ text +   │
 │              │ ◄────────────  │  /stream    │ ◄────────  │ image    │
 └──────────────┘  SSE tokens    └─────────────┘   top-k    └──────────┘
                                       │  ▲
                                  prompt│  │ deltas
                                       ▼  │
                              ┌─────────────────┐
                              │ Doubao-Seed-2.0 │
                              │     -lite       │
                              └─────────────────┘
```

## Components

### 1. iOS client (`client/`)
- **SwiftUI** target iOS 17+; one `ChatViewModel` per conversation (`@Observable`).
- **SSE** consumed via `URLSession.bytes(for:)` → `AsyncStream<ChatDelta>`; cancellation wired to `.task {}` lifecycle.
- **Product card** rendered inline in chat; tap → `ProductDetailView`. Images via `AsyncImage` with `URLCache.shared`.
- **Image upload** path: `PhotosPicker` (iOS 17+) → JPEG compress → multipart POST to `/chat/multimodal`.
- **No API keys** — only knows `PUBLIC_BACKEND_URL`.

### 2. Backend (`server/`)
- **FastAPI** with `uvicorn`. Streaming endpoint emits `text/event-stream` lines.
- **`/chat/stream`** (POST, SSE): { messages, filters? } → tokens + product cards.
- **`/chat/multimodal`** (POST, SSE): multipart (image + text) → same SSE.
- **`/products/{id}`** (GET): detail by id, served from the indexed JSON.
- **`/health`** (GET).
- **FX normalization**: `services/currency.py` fetches and caches latest
  reference quotes for non-CNY source prices, enriches response payloads with
  `price_cny` + `exchange_rate`, and leaves original catalog prices intact.
- **Orchestration**: infer/API-merge retrieval constraints → hybrid RAG → rerank
  → strict converted-CNY budget check → assemble prompt and stream model
  response; product cards are emitted from returned catalog records.
- **Doubao client**: thin wrapper around the ARK API (OpenAI-compatible). Reads key from `.env`.
- **Hardening**: timeout (30s end-to-end), retry on 5xx (backoff 0.5s × 2), per-IP rate limit (defer to v2).

### 3. RAG (`rag/`)
- **Ingest**:
  - `chunk.py`: each product JSON → multiple chunks: `marketing_description`, each `official_faq` entry, each `user_reviews` entry. Each chunk carries `product_id`, `category`, `sub_category`, `brand`, `base_price`, and source `currency`.
  - `embed_text.py`: `BAAI/bge-small-zh-v1.5` embeddings stored in Chroma `products_text`.
  - `embed_image.py`: OpenCLIP ViT-B/32 on A100 for each product main image → Chroma `products_image`.
- **Retrieve**:
  - `constraints.py`: query text and optional API fields → `Filter` for category, subcategory, brand include/exclude, and RMB budget.
  - `query.py` + `bm25.py` + `hybrid.py`: dense and sparse candidate retrieval use the same filter before reciprocal-rank fusion.
  - `rerank.py`: cross-encoder reranking for top-20 → top-5.
- **Prompts**: `prompts/system.md` enforces "answer only from retrieved products, never invent prices/coupons/skus".
- **Eval**: `eval/golden.jsonl` contains 64 audited/regression cases; `python -m rag.eval.report` writes HTML/JSON metrics including scenario slices.

## Data flow per turn

1. iOS sends `{messages: [...], filters?: {}}` to `/chat/stream`.
2. Backend extracts the retrieval query, parses positive constraints and merges
   explicit filter fields. `RAG_HARD_FILTERS=0` disables inference for A/B.
3. Chroma dense retrieval and BM25 apply the same filter, then hybrid fusion
   and reranking produce the candidate list.
4. For an RMB range, indexed CNY prices are filtered early; foreign-priced
   candidates are converted at response time and then checked strictly,
   preserving original price and dated FX metadata.
5. Backend builds prompt: `system_prompt` + `retrieved_context_block` + `conversation_history`.
6. Backend streams Doubao response. Two event types:
   - `data: {"type":"delta","text":"..."}`
   - `data: {"type":"product_card","product":{...}}` — emitted once per cited product, sourced from the indexed JSON (no hallucinated fields).
7. iOS renders deltas into the streaming message bubble; on each `product_card` event, append a card with CNY primary pricing and original-price traceability.

## Multimodal (拍照找货) path

1. iOS picks/captures image, posts to `/chat/multimodal`.
2. Backend runs the image through OpenCLIP (mode depends on deployment: A100 over RPC, or local CPU fallback).
3. Image vector → Chroma `products_image` collection → top-k.
4. Same prompt assembly + streaming as text path, with retrieved products as context.

## Anti-hallucination guarantees

- The model never sees the full product DB; only retrieved context. If retrieval misses, the system_prompt instructs "tell the user you can't find a matching product" rather than guessing.
- Product cards rendered on the client come from indexed JSON, not from model text — the model's job is the *reasoning text*, the cards are *programmatic*.
- Source prices, SKUs, image URLs always come from the indexed data; a
  user-facing CNY amount is derived from a dated, identified FX quote and
  does not overwrite the catalog evidence.

## Deployment

- Chroma persists locally under `data/.chroma/`; Docker provides a reproducible
  Windows validation environment for ingest and evaluation.
- Re-run text ingest after metadata changes such as the newly indexed
  `currency` field required for RMB-aware retrieval filtering.
- A100 used **only** for index-build (CLIP) and batch eval — not the request path.
- iOS client points at the laptop running `uvicorn` (LAN dev) or any host the backend is deployed on.

## Diagram refs

- `sam's sample.png` in the workspace root shows the 5-step flow Sam sketched.
- For the demo video, we'll regenerate a polished version once UI lands.
