# 01 — basic recommendation

**Query**: `推荐一款适合油皮的洗面奶`

**Verdict**: ✅ PASS — top-1 is the right product, system handles the underspecified intent honestly.

## Products returned

Top hits include `p_beauty_011` (珊珂洗颜专科 ¥52) and other 控油 / 油皮-related products. Tujie's synonym expansion ("油皮" → "控油 / 油性肌肤 / 清爽 / 抑油") + the fast-path (brand-aware skip) hit the right candidates without rerank overhead.

## What this verifies

- Synonym expansion firing.
- Cache panel in Settings now shows the request counted toward `total_requests`.
