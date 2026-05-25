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
│   ├── query.py        # Chroma top-k and shared product filter semantics
│   ├── constraints.py  # query/API constraints → retrieval-time Filter
│   ├── bm25.py         # sparse retrieval with the same Filter
│   ├── hybrid.py       # dense + BM25 reciprocal-rank fusion
│   ├── synonyms.py     # reviewed ecommerce query expansion dictionary
│   └── rerank.py       # cross-encoder candidate reranker
├── prompts/
│   └── system.md       # anti-hallucination system prompt template
├── eval/
│   ├── golden.jsonl    # 64 audited and regression cases
│   └── report.py       # HTML/JSON dashboard and scenario metrics
└── requirements.txt
```

## Bring up locally (CPU only)

```bash
# from the repo root
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
python -m rag.ingest.run          # builds the Chroma index (1082 text chunks)
python -m rag.eval.report         # docs/eval_report.html and .json
python tools/build_synonym_candidates.py --terms 无线耳机 降噪 舒敏 控油
```

## Synonym expansion workflow

- Runtime uses `rag/retrieve/synonyms.py`: a small reviewed ecommerce
  dictionary, enabled by `RAG_SYNONYMS=1` by default.
- Offline candidate generation uses `tools/build_synonym_candidates.py`.
  Optional broad synonym packages may contribute candidates, but only reviewed
  terms should be copied into `synonyms.py`.

## Constraint-aware retrieval

- `constraints.py` parses positive category, subcategory, named-brand and RMB
  budget conditions from natural-language queries; explicit API filters take
  precedence over inferred values.
- `Filter` is applied to both Chroma dense candidates and BM25 candidates
  before fusion. `RAG_HARD_FILTERS=0` disables inferred filters for A/B runs.
- `chunk.py` indexes source `currency`. CNY prices can be filtered during
  retrieval; foreign-source candidates remain eligible until the backend
  converts them and applies the RMB range strictly.
- Re-run `python -m rag.ingest.run` after pulling this change because existing
  Chroma metadata does not include `currency`.

## Bring up on the A100 (for CLIP)

```bash
ssh uc
cd ~/shufeng/AAALion-/rag
source ../.venv/bin/activate
python -c "import torch; print(torch.cuda.is_available())"   # expect True
python -m rag.ingest.run                                     # extend to call embed_image
```

## Indexed metadata in Chroma

- `products_text` (text vectors)
  - vector: `BAAI/bge-small-zh-v1.5` embedding
  - metadata: `{product_id, category, sub_category, brand, base_price, currency}`

- `products_image` (image vectors)
  - id: `product_id`
  - vector: 512-d (CLIP ViT-B/32)
  - payload: `{product_id, category, brand, base_price}`
