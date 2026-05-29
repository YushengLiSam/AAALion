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

## Run with Docker

```bash
cd server
cp ../.env.example .env
sed -i 's/^LLM_PROVIDER=.*/LLM_PROVIDER=echo/' .env
docker compose build backend
docker compose run --rm --no-deps backend python -m rag.ingest.run
docker compose up -d
until curl -fsS http://localhost:8000/ready; do sleep 1; done
```

Backend at `http://localhost:8000`. The current text-retrieval path persists
Chroma under `data/.chroma/`; rebuild that index with `python -m rag.ingest.run`
after changing indexed metadata. The Docker build stores embedding and
cross-encoder weights in the image; startup performs a complete retrieval
warmup before `/ready` succeeds or chat traffic is accepted.
For a copy-and-run Windows PowerShell deployment and TokenRouter switch
instructions, see the root [`README.md`](../README.md#docker-deployment-on-windows-copy-and-run).

## Layout

```
app/
├── main.py            # FastAPI app + CORS + static mount
├── config.py          # Settings dataclass, .env loader
├── routes/
│   ├── health.py      # GET /health + /ready
│   ├── chat.py        # POST /chat/stream (SSE)
│   └── products.py    # GET /products/{id}
├── schemas/
│   └── chat.py        # Pydantic request models
└── services/
    ├── llm_provider.py    # TokenRouter / Anthropic / Doubao / OpenAI switch
    ├── rag_client.py      # Hybrid retrieval + rerank + intent handling
    ├── retrieval_readiness.py # Startup model/query-path prewarm
    └── currency.py        # Latest-reference foreign-price conversion to CNY
```

## What works today

- `/health` returns 200.
- `/ready` returns 200 only after retrieval prewarm; `/chat/stream` rejects
  traffic with 503 until then.
- `/chat/stream` runs RAG retrieval, streams LLM deltas, then sends product cards.
- `/products/{id}` returns product details enriched with display pricing.
- Static images served from `/static/...`.
- Foreign-source prices are displayed in CNY using the latest available
  Frankfurter reference quote, while original price/currency and rate date
  remain in the payload.
- Text queries and optional request filters constrain category, subcategory,
  included/excluded brands and RMB price bounds before hybrid retrieval.
- Multi-turn constraints inherit, replace or cancel budget/brand conditions
  without restoring stale anchor text.

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

## Retrieval constraints

`/chat/stream` accepts optional `filters` fields: `category`, `sub_category`,
`price_min`, `price_max`, `include_brands`, and `exclude_brands`. The backend
also infers positive constraints from text such as `3500元以下的 Sony 降噪耳机`.
Both dense and BM25 retrieval see the same constraint object.

`RAG_HARD_FILTERS=1` is enabled by default. Set it to `0` only for evaluation
or diagnosis. When a query supplies a RMB budget, foreign-source items are
converted using the current FX quote before the final strict range check.

`RAG_PREWARM=1` is enabled by default. It performs model and full-query
initialization during server startup; set it to `0` only for diagnosis.

## Quick smoke

```bash
curl -s http://localhost:8000/health
until curl -fsS http://localhost:8000/ready; do sleep 1; done
curl -s -N -X POST http://localhost:8000/chat/stream \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"推荐一款油皮的洗面奶"}]}'

# Foreign-priced detail: shows base_price (USD), price_cny and exchange_rate
curl -s http://localhost:8000/products/p_2_intl_01
```
