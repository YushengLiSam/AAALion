# server/ — FastAPI backend

Owner: **Sam (Yusheng Li)**.

## Run locally

```bash
cd server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env   # fill in DOUBAO_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Run with Docker (includes Qdrant)

```bash
cd server
cp ../.env.example .env
docker compose up --build
```

Backend at `http://localhost:8000`, Qdrant dashboard at `http://localhost:6333/dashboard`.

## Layout

```
app/
├── main.py            # FastAPI app + CORS + static mount
├── config.py          # Settings dataclass, .env loader
├── routes/
│   ├── health.py      # GET /health
│   ├── chat.py        # POST /chat/stream (SSE)
│   └── products.py    # GET /products/{id}
├── schemas/
│   └── chat.py        # Pydantic request models
└── services/
    ├── llm_provider.py    # TokenRouter / Anthropic / Doubao / OpenAI switch
    ├── rag_client.py      # Hybrid retrieval + rerank + intent handling
    └── currency.py        # Latest-reference foreign-price conversion to CNY
```

## What works today

- `/health` returns 200.
- `/chat/stream` runs RAG retrieval, streams LLM deltas, then sends product cards.
- `/products/{id}` returns product details enriched with display pricing.
- Static images served from `/static/...`.
- Foreign-source prices are displayed in CNY using the latest available
  Frankfurter reference quote, while original price/currency and rate date
  remain in the payload.

## Currency conversion

No API key is needed for FX. Optional `.env` settings:

```bash
FX_API_BASE_URL=https://api.frankfurter.dev/v2
FX_RATE_TTL_SECONDS=3600
FX_HTTP_TIMEOUT_SECONDS=3.0
```

`base_price` remains the source amount. `price_cny` and `exchange_rate` are
response-time fields used by the iOS display and CNY price-intent logic. The
rate is informational, not a payment settlement quote. If neither a live nor
cached quote is available, the foreign source amount remains visible and is
not treated as satisfying a RMB budget filter.

## Quick smoke

```bash
curl -s http://localhost:8000/health
curl -s -N -X POST http://localhost:8000/chat/stream \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"推荐一款油皮的洗面奶"}]}'

# Foreign-priced detail: shows base_price (USD), price_cny and exchange_rate
curl -s http://localhost:8000/products/p_2_intl_01
```
