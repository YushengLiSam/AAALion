# Data

## Current state

`data/seed/` contains 100 product entries across four categories: 美妆护肤, 数码电子, 服饰运动, 食品生活. Each product has:

- `product_id`, `title`, `brand`, `category`, `sub_category`
- `base_price` (CNY) and a `skus` list (variants)
- `image_path` (relative to `data/seed/<category>/images/`)
- `rag_knowledge`:
  - `marketing_description` (~200 chars)
  - `official_faq`: 2-3 question/answer pairs
  - `user_reviews`: 3-5 entries with rating + content

**Important**: this seed dataset was confirmed by the competition recruiters to be **AI-generated**. It works as a smoke-test set and to develop pipelines against, but the demo and final eval must use **real** product data — otherwise the judges may discount our work.

## Where to find real data

### Strategy A — discover existing datasets via Perplexity (recommended first step)

Paste this into Perplexity:

> Find publicly available datasets of real Chinese e-commerce product listings from Taobao, JD.com, Tmall, or Pinduoduo that include: product title, brand, category, price, main image URL, and ideally marketing description + user reviews. Limit to datasets released 2022 or later. For each dataset, give me: name, host (HuggingFace / Kaggle / GitHub / academic site), license, size, exact download URL, and a one-sentence note on quality (curated vs. scraped). Prioritize ones that include images and ones whose license permits research use. Do not invent datasets — only list ones with a verifiable URL.

And for APIs:

> List public or semi-public APIs that return real product data for Chinese e-commerce (Taobao Open Platform, JD Union, Pinduoduo Open Platform, Xiaohongshu Notes API, Douyin commerce API). For each: auth requirement, rate limit, whether sandbox/test data is real or synthetic, and whether a college student team can register without a business license. Include any unofficial mirrors or aggregated datasets on GitHub that have current real data dumps.

And for image datasets:

> Find image-text product datasets useful for training or evaluating a multimodal e-commerce search system, with real product photos and Chinese text. Examples I'm looking to confirm or beat: M5Product, RPC, Products10K, Fashion-Gen. For each, give license, size, image count, whether Chinese text is present, and download URL. Add any newer (2024-2026) alternatives you can verify.

### Strategy B — normalize a discovered dataset with Gemini

Once Perplexity returns a candidate, paste a sample row into Gemini with:

> Here is a dataset description and a sample row: [paste]. Write a Python script using `pandas` (or `datasets` for HuggingFace) that downloads it, filters to rows that have ALL of {title, brand, price (CNY), image_url, description longer than 100 chars}, and emits one JSON file per product matching this schema: [paste `data/seed/1_美妆护肤/data/p_beauty_001.json`]. Use real values from the dataset; do not fabricate FAQ entries — if real FAQ/reviews are not in the source, leave those fields empty.

Drop the resulting JSONs into `data/extra/` (gitignored — large, license-uncertain).

### Strategy C — direct sources to check

- **HuggingFace**: search `chinese ecommerce`, `taobao`, `product`. Likely candidates: `Multimodal-Fatima/M5Product`, `BAAI/CCI`.
- **Kaggle**: search `taobao`, `jd.com`, `tmall`.
- **GitHub**: `awesome-chinese-nlp`, `awesome-ecommerce-datasets` curated lists. Active spiders (verify legality before running).
- **Academic**: M5Product (CVPR 2022), Products10K, RPC (Retail Product Checkout).
- **Official**: Taobao Open Platform, JD Union, Pinduoduo Open Platform — all require registration; check whether each member's individual real-name verification is enough or whether a business entity is required.

### Strategy D — manual curation (fallback floor)

If A/B/C don't land by **2026-06-01**, the team manually curates **30-50 real products**: browse Tmall / JD, copy real titles + descriptions + images + reviews into the same JSON schema. ~4 hours total split three ways. Unambiguously real and license-clean for internal use.

## Schema reference

See `data/seed/1_美妆护肤/data/p_beauty_001.json` for the canonical shape. Any new data must match this shape exactly (the RAG ingest is keyed on these field names).

## Adversarial test queries

Synthetic is fine here (we're stress-testing the model, not pretending to be real data). Use this Gemini/Perplexity prompt:

> Generate 30 challenging user queries a Chinese e-commerce shopper might ask an AI assistant, covering: (1) ambiguous intent ('便宜点的'), (2) negation ('不要含酒精的'), (3) multi-product comparison ('A 和 B 哪个更适合'), (4) budget + feature constraints ('预算500, 防水蓝牙耳机'), (5) follow-up refinement (multi-turn). Format as a JSON list of {query, expected_behavior} pairs.

Resulting file → `rag/eval/golden.jsonl` (with expected `product_ids` filled in by hand after running them against the index).
