# 01 — What is RAG, and why don't we just use ChatGPT?

## What is this?

**RAG** stands for **Retrieval-Augmented Generation**. It's a technique
for making a large language model (LLM, the same kind of thing that powers
ChatGPT) answer questions about **our** data instead of whatever it
memorized during training.

In plain English: we don't trust the LLM to know our product catalog. So
every time the user asks something, we first look up the most relevant
products from our database, then give those products to the LLM along
with the user's question. The LLM's job becomes "write a nice reply,
using ONLY these products". It can no longer invent fake stuff.

## Why does it matter?

LLMs like ChatGPT have two well-known failure modes:

1. **They hallucinate.** Ask "what's the price of the Sony WH-1000XM5?"
   and a stock LLM might confidently say "$349" when the real price is
   $398. It's not lying on purpose — it just generates plausible-sounding
   text without checking anything.

2. **They don't know our stuff.** Our app sells 145 specific products
   curated from Amazon US and Chinese platforms. ChatGPT has never seen
   our catalog. Even if it had been trained on Amazon's site, our
   catalog snapshots from a specific date — prices change.

For a shopping app, both failure modes are deal-breakers. If the AI
recommends a product that doesn't exist, the user gets confused. If it
quotes the wrong price, you lose user trust permanently.

**RAG fixes both.** By forcing the LLM to answer using only the products
we hand it, we control the catalog and the prices.

## How we built it

The flow looks like this:

```
User: "推荐一款适合油皮的洗面奶"
        │
        ▼
[1] Retrieval — find the most relevant products from our database.
    Returns 3-5 products with full information.
        │
        ▼
[2] Build a "system prompt" that tells the LLM:
    "You are a shopping assistant. Here is the catalog. Only answer
     using these products. Don't make up anything."
        │
        ▼
[3] Send the system prompt + user's question to the LLM.
    LLM generates a reply.
        │
        ▼
User sees: "我为您推荐珊珂洗颜专科 ¥52 …" + product cards
```

The retrieval step is the most important part. It's what makes this
"RAG" instead of "just an LLM with a longer prompt". We have several
ways to find products (see [`02-finding-products.md`](02-finding-products.md)
for the full hybrid-search story). Whichever method we use, the goal is
the same: return the 3-5 products most likely to answer the user's
question.

In our code, the retrieval lives in `rag/retrieve/query.py` (the
orchestrator) and `server/app/services/rag_client.py` (the wrapper the
chat route calls). The system prompt that tells the LLM "don't make up
products" is in `server/app/routes/chat.py`, around the `_PROMPT` constant
— here are the relevant lines:

```
你是一名中文电商导购助手。仅基于下面的商品目录回答；目录中没有的商品、价格、优惠绝对不要编造。
…
## 商品目录
{catalog}
```

The `{catalog}` placeholder gets filled in with the retrieved products
formatted as text (each line: `product_id | title | brand | price | description`).
The LLM gets this whole block as context, then generates the reply.

## What happens when retrieval fails?

If the user asks "推荐一台量子计算机" (recommend a quantum computer), our
retrieval returns the closest products it can find — laptops, tablets.
The system prompt instructs the LLM to be honest: "目录中没有 X, 但你可以看
看 Y." This is RAG's superpower — when there's no good answer, the model
says so instead of inventing one.

## Where to dig deeper

- `server/app/routes/chat.py` — the chat endpoint, lines 50-80 hold the
  system prompt; lines 220-280 hold the retrieval + LLM orchestration.
- [`02-finding-products.md`](02-finding-products.md) — how the retrieval
  step actually works (it's surprisingly sophisticated).
- [`13-what-numbers-mean.md`](13-what-numbers-mean.md) — how we measure
  whether RAG is doing its job correctly.
- `docs/ARCHITECTURE.md` — the system-design view for engineers.
