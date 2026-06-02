# API Reference

The backend exposes a small HTTP surface. iOS only knows these endpoints. Keep this file synced with `server/app/main.py`.

## Base URL

Development: `http://localhost:8000`. Configure in `client/AAALionApp/AAALionApp/Config.swift` (`PUBLIC_BACKEND_URL`).

## Endpoints

### `GET /health`

Liveness check.

**Response 200**:
```json
{ "status": "ok", "version": "0.1.0" }
```

---

### `GET /ready`

Readiness check for user traffic. The backend warms embedding, BM25, the
cross-encoder reranker and one complete retrieval call at startup.

**Response 200** after warmup:
```json
{"status":"ready","retrieval":{"status":"ready","prewarm":"completed","embedding":"ready","bm25":"ready","reranker":"ready","query_path":"ready"}}
```

**Response 503** if startup warmup failed. `/chat/stream` also returns `503`
until this endpoint is ready, so a user request never pays the model-load cost.

---

### `POST /chat/stream`

Multi-turn chat with streaming response.

**Request**:
```json
{
  "messages": [
    {"role": "user", "content": "推荐一款适合油皮的洗面奶"},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "再便宜点的呢"}
  ],
  "filters": {
    "category": "美妆护肤",
    "sub_category": "洁面",
    "price_min": 0,
    "price_max": 200,
    "include_brands": ["珂润"],
    "exclude_brands": []
  }
}
```

`filters` is optional. Supported fields are `category`, `sub_category`,
`price_min`, `price_max`, `include_brands`, and `exclude_brands`. The backend
also infers positive constraints from text such as `3500元以下的 Sony 降噪耳机`;
explicit request fields override inferred values.

For multi-turn requests, inferred constraints persist across user messages and
are updated before retrieval. Follow-ups such as `预算加到3500元`, `不要 Sony
了，改成 Bose`, `品牌不限`, or `预算不限` replace or cancel prior state
without requiring the client to construct `filters` itself. Sending explicit
`filters` remains the authoritative override for the current request.

**Response**: `text/event-stream`. Each event is a JSON object, one per `data:` line:

```
data: {"type":"delta","text":"为你推荐"}

data: {"type":"delta","text":"这款洁面"}

data: {"type":"product_card","product":{"product_id":"p_2_intl_01","title":"索尼 WH-1000XM5 头戴降噪耳机 黑色","brand":"Sony","base_price":398.0,"price_cny":2703.06,"exchange_rate":{"source_currency":"USD","target_currency":"CNY","rate":6.7916,"rate_date":"2026-05-25","provider":"Frankfurter latest reference rate","stale":false},"provenance":{"currency":"USD","source_platform":"Amazon US"},"image_url":"..."}}

data: {"type":"done"}
```

Event types:

| `type` | Payload | Meaning |
|---|---|---|
| `product_card` | `{"product": {...}}` | Show a product card. **R10**: emitted *before* the `delta` text (首屏极速 — cards lead the LLM) |
| `delta` | `{"text": "..."}` | Token(s) to append to the streaming message |
| `cart_intent` | `{"action": "...", "index": Int?, "quantity": Int?}` | Conversational cart op. `action` ∈ `add` · `checkout` · `remove` · **`set_quantity`** (R10). `index` is a 1-based ordinal (`-1` = last) for remove/set_quantity; `quantity` is the target for set_quantity (e.g. "把数量改成2") |
| `clarify` | `{"chips": ["...", ...]}` | **R10 #5** — request was too vague; the reply is a clarifying question and these are tappable quick-reply chips (no product cards this turn) |
| `claim_summary` | `{"verified": Int, "inferred": Int}` | Per-message provenance tally for the `[目录✓]`/`[推断?]` markers |
| `error` | `{"message": "...","code":"..."}` | Show an error toast; stream is over |
| `done` | `{}` | Stream complete, OK to finalize the bubble |

Connection ends with a `data: {"type":"done"}` event followed by EOF.

Price fields:

| Field | Meaning |
|---|---|
| `base_price` | Original catalog amount in `provenance.currency`; never overwritten |
| `price_cny` | User-facing RMB amount. Equal to `base_price` for CNY products; converted for foreign products when FX is available |
| `exchange_rate` | Present for converted foreign items: source/target currency, rate, reference date, provider, and `stale` fallback flag |

Foreign conversion uses the latest available reference rate from
[Frankfurter v2](https://frankfurter.dev/) and is cached server-side for one
hour. It supports comparable display and RMB budget filtering; it is not a
payment settlement quote. If neither a fresh nor cached quote is available,
the client shows the original currency instead of fabricating a CNY value.
For retrieval, CNY catalog items are budget-filtered at candidate recall time.
Foreign-source items are retained until conversion and then checked strictly
against the same RMB range.

---

### `POST /chat/multimodal`

Same shape as `/chat/stream` but accepts an image. Used for 拍照找货.

**Request**: `multipart/form-data`
- `image`: file (JPEG/PNG, max 5 MB)
- `messages`: JSON string (same as `/chat/stream`)
- `filters`: JSON string, optional

**Response**: same SSE stream as `/chat/stream`.

---

### `GET /products/{product_id}`

Product detail by id. Used by the iOS `ProductDetailView` after a card tap.

**Response 200**:
```json
{
  "product_id": "p_2_intl_01",
  "title": "索尼 WH-1000XM5 头戴降噪耳机 黑色",
  "brand": "Sony",
  "category": "数码电子",
  "base_price": 398.0,
  "price_cny": 2703.06,
  "provenance": {"currency": "USD", "source_platform": "Amazon US"},
  "exchange_rate": {
    "source_currency": "USD",
    "target_currency": "CNY",
    "rate": 6.7916,
    "rate_date": "2026-05-25",
    "provider": "Frankfurter latest reference rate",
    "stale": false
  },
  "skus": [{"price": 398.0, "price_cny": 2703.06}],
  "rag_knowledge": {
    "marketing_description": "...",
    "official_faq": [...],
    "user_reviews": [...]
  }
}
```

The response above is an illustrative captured quote. `price_cny` and
`exchange_rate.rate` depend on the provider's latest available quote and can
be refreshed even within the same rate date; they are not fixed catalog data.

**Response 404**:
```json
{ "detail": "product not found" }
```

---

### `GET /static/images/<filename>`

Static product image. The server mounts `data/seed/<category>/images/` here for the demo.

## Errors

Standard HTTP codes. For SSE, errors are emitted as a `data: {"type":"error","message":"...","code":"..."}` event rather than HTTP status (the connection is already 200 OK by then).

Common codes:
- `RATE_LIMITED` — Doubao rate limit hit; client should retry after backoff.
- `RETRIEVAL_EMPTY` — RAG returned no products; model will say so to the user.
- `UPSTREAM_TIMEOUT` — Doubao took >30s.

## Versioning

Pre-1.0. Breaking changes require updating both client and backend in the same PR.
