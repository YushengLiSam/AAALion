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
    ├── doubao_client.py   # ARK / OpenAI-compatible client (stub)
    └── rag_client.py      # Wrapper around rag/ — currently a keyword stub
```

## What works today

- `/health` returns 200.
- `/chat/stream` streams a hard-coded fixture (lets the iOS team start).
- `/products/{id}` returns the indexed JSON for any product in `data/seed/`.
- Static images served from `/static/...`.

## What's stubbed

- `services/doubao_client.py` — `NotImplementedError` until Sam wires the real ARK call.
- `services/rag_client.py` — currently a keyword-overlap heuristic. Tujie will swap in the real Qdrant retriever.
- The chat route returns a fixture; will be replaced with `rag_client.stub_top_k(...)` → prompt assembly → `doubao_client.stream_chat(...)`.

## Quick smoke

```bash
curl -s http://localhost:8000/health
curl -s -N -X POST http://localhost:8000/chat/stream \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"推荐一款油皮的洗面奶"}]}'
```
