# 04 — multi-turn ("再便宜点的呢")

**Conversation**:
1. user: `推荐一款洗面奶`
2. assistant: `推荐 珊珂洁面 ¥52`
3. user: `再便宜点的呢`

**Verdict**: ✅ PASS — context inherited, cheapest-first.

## Assistant response, turn 3 (excerpt)

Returns `p_beauty_011` (珊珂 ¥52). Tujie's `contextual_query` correctly recognized turn-3 as a follow-up, pulled "洗面奶" anchor from turn 1, and Tujie's `price_intent` sorted cheapest-first. Then `stateful constraint` carries that category if a follow-up adds a budget filter (see scenario #08).

## Product returned

| ID | Title | Brand | Price |
|---|---|---|---|
| `p_beauty_011` | 珊珂洗颜专科绵润泡沫洁面乳 | 珊珂 | ¥52 |

(only 1 result because 珊珂 is the cheapest 洗面奶 in the catalog; rerank kept it solo)
