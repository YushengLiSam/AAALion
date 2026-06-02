# 02 — How we find the right products

## What is this?

When the user types "推荐一款适合油皮的洗面奶" (recommend a face wash for oily
skin), we need to pick 3-5 products from our catalog of 145 to show them.
This is called **retrieval**. We use a combination of three different
methods, each of which catches things the others miss. The combined
approach is called **hybrid retrieval**, and the final ranking pass is
called **reranking**.

## Why does it matter?

If we just typed "油皮" (oily skin) into a regular database search, we'd
only find products whose description literally contains the word "油皮".
A face wash labeled "适合油性肌肤" (suitable for oily skin) — different
words, same meaning — would be missed.

If we asked an AI model "give me the 5 most similar products to this query",
it might miss products that match by EXACT keyword (like a brand name the
user typed). It's also weirdly sensitive to phrasing.

Each method on its own has blind spots. Combining them gets us the best of
all worlds.

## How we built it

### Method 1: Keyword search (BM25)

**BM25** is a classic algorithm from 1994. It scores documents by how
often the query words appear, with a bias toward rarer words being more
informative. For us: if the user types "Sony WH-1000XM5", BM25 finds the
Sony headphone because the brand name is right there.

Chinese needs special handling because Chinese doesn't have spaces between
words. We use a library called **jieba** to break "适合油皮的洗面奶" into
"适合 / 油皮 / 的 / 洗面奶" before BM25 sees it.

Code: `rag/retrieve/bm25.py`.

### Method 2: Semantic search (dense embeddings)

Words can mean similar things without sharing characters. "洗面奶" and
"洁面乳" both mean "face wash" in Chinese. BM25 has no idea. Semantic
search does.

Here's the trick: we use a neural network called **BGE-zh** (specifically
`BAAI/bge-small-zh-v1.5`, a Chinese-language model) to turn every product
description into a 512-number vector. Vectors close to each other in this
space mean "these products are about similar things". Same trick on the
user's query: we turn the query into a vector, then find products whose
vectors are closest.

This catches cases where the words don't overlap. "敏感肌肤温和清洁" might
match a product described as "适合敏感人群的低刺激配方" even though no word
is the same.

Code: `rag/ingest/embed_text.py` (builds the vectors at startup),
`rag/store.py` (stores them in a vector database called Chroma).

### Combining them: RRF fusion

Each method gives a ranked list. We combine them with **Reciprocal Rank
Fusion (RRF)** — a fancy way of saying "give each product a score based
on its position in each list, then add the scores". A product that ranks
3rd in BM25 and 2nd in dense search gets a high combined score. A product
ranked 1st in only one list gets a medium score. A product not in either
list gets nothing.

Code: `rag/retrieve/hybrid.py`.

### Method 3: Reranking

The two methods above are fast but imprecise. After the hybrid step we
have a list of ~10 candidate products. We pass these through a third
neural network — a **cross-encoder reranker** called `bge-reranker-base`
— that looks at each (query, product) pair individually and scores how
well they match.

Cross-encoders are slow (you have to call the model once per candidate)
but more accurate. We only call it on 10 candidates from the hybrid step,
not on the whole catalog of 145, so the slowness is manageable.

Code: `rag/retrieve/rerank.py`. The reranker takes the top 10 from
hybrid and re-sorts them, then we keep the top 3-5.

### The full pipeline

```
Query: "推荐一款适合油皮的洗面奶"
        │
        ├──► BM25 (jieba-tokenized keyword match) ──► list A
        │
        ├──► BGE-zh dense embedding similarity ────► list B
        │                                            │
        ├──► RRF fusion of lists A and B ────────────┤
        │                                            │
        ▼                                            ▼
   top ~10 candidates ──────► bge-reranker-base ──► top 3-5
                              (cross-encoder)
```

Total time: about 300 ms on a Mac. The reranker is the slowest part
(~150 ms for 10 candidates on CPU). When the user's query is very
specific — they named an exact product, like "Sony WH-1000XM5" — we
skip the reranker (it costs time without changing the answer). This
fast-path lives in `server/app/services/rag_client.py` as
`_is_specific_query()`.

## Why three methods instead of one big model?

Bigger LLMs can do retrieval in a single pass ("read all 145 products
and pick the top 5"), but they cost much more per query and aren't
fundamentally better for this scope. Our hybrid pipeline runs in 300 ms
on CPU and gives us **recall@5 = 0.880** on a 59-case audited test set
(meaning: in 88% of test queries, the correct product was in our top 5).
That's competitive with the more expensive approaches.

## Where to dig deeper

- `rag/retrieve/query.py` — the orchestrator that calls all three
  methods in order.
- `rag/retrieve/hybrid.py` — the RRF fusion math.
- `rag/retrieve/rerank.py` — the cross-encoder.
- [`12-how-we-measure.md`](12-how-we-measure.md) — how we know our
  retrieval is good.
- `docs/EVAL_RESULTS.md` — the engineer-facing eval report.
