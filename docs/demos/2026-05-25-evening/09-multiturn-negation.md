# 09 — multi-turn negation persistence (R8 B2 fix)

**Conversation**:
1. user: `推荐防晒霜不要日系`
2. assistant: `推荐 巴黎欧莱雅 ¥170`
3. user: `再便宜点的呢`

**Verdict**: ✅✅ PASS — **this is the demo that proves R8 B2 closes a real bug.**

## Before R8 (broken)

In the prior pipeline, turn 3 had no `不要` signal → the LLM negation extractor wasn't invoked → the `exclude_keywords: ["日系", "日本"]` from turn 1 was LOST. Result: Japanese-brand products like 安热沙 / 资生堂 leaked back into top-5.

## After R8 (fixed)

`Filter.exclude_keywords` (R8) carries country-trigger keywords across turns the same way `brand_exclude` already did. `constraint_state._merge_turn` unions them. `top_k` invokes `apply_negation` whenever the conversation filter holds keywords, even when the current turn has no `不要` signal.

## Products returned, turn 3

| ID | Title | Brand | Resolved origin |
|---|---|---|---|
| `p_beauty_006` | 巴黎欧莱雅新多重防护隔离露 | 巴黎欧莱雅 | 🇫🇷 FR |
| `p_beauty_023` | 理肤泉特护清盈防晒乳 | 理肤泉 | 🇫🇷 FR |

**Zero Japanese brands.** The `不要日系` from turn 1 still applies.

## What this verifies

- `build_retrieval_filter` populated `exclude_keywords = ["日系", "日本"]` from turn 1's text.
- `_merge_turn` carried it forward to turn 3.
- `top_k` saw `inherited_keywords` non-empty + no current-turn negation → still called `apply_negation` with `exclude_keywords` → brand-origin lookup dropped JP products.
- Golden test case `multiturn + negation + brand-origin` scores recall@5 = 1.000, neg-acc = 1.000.

## Cancellation

To clear the inherited country exclusion mid-conversation, the user can say "国别不限" / "不限国别" — `_CLEAR_KEYWORDS_RE` in `constraint_state.py` catches this and resets `exclude_keywords` to None.
