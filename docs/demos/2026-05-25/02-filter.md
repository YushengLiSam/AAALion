# 02 — price filter

**Query**: `200元以下的蓝牙耳机有哪些`

**Verdict**: ✅ PASS — Tujie's `price_intent` parses `200元以下` into hard `price_max_cny=200`; `apply_product_filter(strict_cny_price=True)` drops everything else.

## What's new from R7

- R7 had a `price_max` filter but didn't normalize foreign prices first.
- R8 (Tujie R7.4): foreign-source products get FX-normalized to CNY BEFORE the price filter is enforced. So a $249 product is correctly compared against ¥200, not $200.

If no product fits, the LLM is told the filter eliminated all candidates and responds honestly with "目录中无 200元以下蓝牙耳机".
