# rag/ — Retrieval and indexing

Owner: **Tujie Guan**.

## Layout

```
rag/
├── ingest/
│   ├── chunk.py        # product JSON → list[Chunk] (desc | faq | review)
│   ├── embed_text.py   # Doubao-embedding-vision (stub)
│   ├── embed_image.py  # OpenCLIP ViT-B/32 on A100 (stub)
│   └── run.py          # python -m rag.ingest.run
├── retrieve/
│   ├── query.py        # top-k with filters (keyword fallback today)
│   ├── synonyms.py     # reviewed ecommerce query expansion dictionary
│   └── rerank.py       # optional reranker (identity today)
├── prompts/
│   └── system.md       # anti-hallucination system prompt template
├── eval/
│   ├── golden.jsonl    # 10 seed cases; grow to 30+ before 06-05
│   └── run.py          # python -m rag.eval.run → recall@5
└── requirements.txt
```

## Bring up locally (CPU only)

```bash
# from the repo root
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # torch is fine on CPU but heavy; skip if just iterating on retrieve
python -m rag.ingest.run          # uses the fallback embedder (zero-vectors)
python -m rag.eval.run            # recall@5 against the keyword retriever
python tools/build_synonym_candidates.py --terms 无线耳机 降噪 舒敏 控油
```

## Synonym expansion workflow

- Runtime uses `rag/retrieve/synonyms.py`: a small reviewed ecommerce
  dictionary, enabled by `RAG_SYNONYMS=1` by default.
- Offline candidate generation uses `tools/build_synonym_candidates.py`.
  Optional broad synonym packages may contribute candidates, but only reviewed
  terms should be copied into `synonyms.py`.

## Bring up on the A100 (for CLIP)

```bash
ssh uc
cd ~/shufeng/AAALion-/rag
source ../.venv/bin/activate
python -c "import torch; print(torch.cuda.is_available())"   # expect True
python -m rag.ingest.run                                     # extend to call embed_image
```

## To-dos (Tujie)

- [ ] Wire `embed_text.embed_chunks` to the real Doubao embedding endpoint.
- [ ] Wire `embed_image.embed_images` to OpenCLIP and produce `products_image` vectors.
- [ ] Upsert into Qdrant in `ingest/run.py` (text + image collections).
- [ ] Replace `retrieve.query.query` with Qdrant search + payload filters.
- [ ] Optional: cross-encoder rerank in `rerank.rerank`.
- [ ] Grow `eval/golden.jsonl` to 30+ cases with real expected ids.

## Schema in Qdrant

- `products_text` (text vectors)
  - id: `chunk_id` (uuid)
  - vector: 768-d (Doubao embedding)
  - payload: `{product_id, chunk_type, category, sub_category, brand, base_price, text}`

- `products_image` (image vectors)
  - id: `product_id`
  - vector: 512-d (CLIP ViT-B/32)
  - payload: `{product_id, category, brand, base_price}`
