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
    "price_max": 200,
    "exclude_brands": []
  }
}
```

`filters` is optional. The backend may also infer filters from the message text.

**Response**: `text/event-stream`. Each event is a JSON object, one per `data:` line:

```
data: {"type":"delta","text":"为你推荐"}

data: {"type":"delta","text":"这款洁面"}

data: {"type":"product_card","product":{"product_id":"p_beauty_004","title":"...","brand":"...","base_price":89,"image_url":"..."}}

data: {"type":"done"}
```

Event types:

| `type` | Payload | Meaning |
|---|---|---|
| `delta` | `{"text": "..."}` | Token(s) to append to the streaming message |
| `product_card` | `{"product": {...}}` | Show a clickable product card in the conversation |
| `error` | `{"message": "...","code":"..."}` | Show an error toast; stream is over |
| `done` | `{}` | Stream complete, OK to finalize the bubble |

Connection ends with a `data: {"type":"done"}` event followed by EOF.

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
  "product_id": "p_beauty_004",
  "title": "...",
  "brand": "...",
  "category": "美妆护肤",
  "sub_category": "...",
  "base_price": 89.0,
  "image_url": "http://localhost:8000/static/images/p_beauty_004_live.jpg",
  "skus": [...],
  "rag_knowledge": {
    "marketing_description": "...",
    "official_faq": [...],
    "user_reviews": [...]
  }
}
```

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
