# 08 — stateful constraint inheritance (Tujie R7.5)

**Conversation**:
1. user: `推荐一款数码电子产品`
2. assistant: `目录里有耳机、笔记本、手机等`
3. user: `5000元以下的呢`

**Verdict**: ✅ PASS — category inherited from turn 1; budget applied from turn 3.

## Products returned (top 5, all 数码电子, all ≤ ¥5000)

| ID | Title | Brand | CNY price |
|---|---|---|---|
| `p_2_intl_02` | 苹果 AirPods Pro 二代 USB-C | Apple | ~¥1782 (after FX from $249) |
| `p_2_intl_03` | 任天堂 Switch OLED 白色款主机 | Nintendo | ~¥2503 (from $349.99) |
| `p_2_intl_01` | 索尼 WH-1000XM5 头戴降噪耳机 | Sony | ~¥2848 (from $398) |
| `p_digital_007` | 华为 FreeBuds Pro 5 | 华为 | ¥1699 |
| `p_digital_018` | Apple AirPods Pro 3 | Apple | ¥1899 |

## What this verifies

- **Constraint inheritance**: turn 3 didn't say "数码电子" but inherited it from turn 1.
- **Budget filter applied to BOTH local CNY and FX-normalized foreign prices** — Tujie's `apply_product_filter(strict_cny_price=True)` correctly evaluates `priceCNY` (post-FX) against the budget.
- **No off-category leakage** (no cosmetics / books).

## Note

A tighter budget like `300元以下` returns empty — legitimate, because all CN 数码 products start at ¥1699 and all USD-priced items also exceed ¥300 after FX. That's correct system behavior, not a bug. The 5000元 budget demonstrates the inherited category + budget interaction more clearly.
