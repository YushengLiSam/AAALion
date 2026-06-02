# 03 — negation (single-turn, R7 fix carried forward)

**Query**: `推荐防晒霜，不要日系品牌，不要含酒精`

**Verdict**: ✅ PASS — brand-origin filter excludes JP brands.

## Products returned (top 5)

All 法系 / 美系 / 国货, no Japanese. Pipeline:
1. LLM negation extractor (or local fallback) outputs `exclude_keywords: ["酒精", "日系", "日本"]`.
2. `brand_origin.excluded_countries(["酒精", "日系", "日本"])` resolves `{JP}`.
3. `product_origin(product)` resolves each candidate via `provenance.origin_country` OR `brand_origin.lookup(brand)`.
4. Products with origin == JP are dropped.

Sam's R7-evening audit pushed this from 0.733 → **1.000** negation accuracy.

## What persists post-R8

The same brand-origin filter now also persists into multi-turn conversations (see scenario #09).
