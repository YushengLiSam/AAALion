# 04 — How "再便宜点的呢" remembers what we were just talking about

## What is this?

In a real conversation, people don't repeat themselves. They say:

> "推荐一款洗面奶"
> *(the app suggests three face washes)*
> "再便宜点的呢"
> *(the user expects: cheaper face washes — NOT cheaper laptops or cheaper coats)*

The follow-up "再便宜点的呢" only makes sense in the context of the
previous turn. Most search engines treat each query as fresh and would
return random cheap stuff. We don't.

This explainer covers how we make multi-turn conversations work, and
also where the current implementation still has a known bug.

## Why does it matter?

Shopping is rarely a one-shot question. Real users iterate:

```
Turn 1: 推荐防晒霜
Turn 2: 不要日系
Turn 3: 预算 300 以下
Turn 4: 跟 SPF50+ 的
Turn 5: 那有没有不要油腻的
```

Each turn adds constraints. If we lost the constraints between turns,
the user would have to type the full request every time. By turn 5 it
would be a sentence. Nobody does that — they'd give up.

## How we built it

### The stateful filter idea

We keep a **conversation filter** that accumulates constraints across
turns. Each turn does three things:

1. Take the previous filter (whatever's been carried forward).
2. Extract new constraints from the current message (price, brand,
   exclusions).
3. Merge: inherit what should persist, override what should change.

The result is a single filter object that represents "everything the
user has asked for so far". Retrieval uses this filter, not just the
current turn.

Code: `server/app/services/constraint_state.py`. The key function is
`build_conversation_filter(messages)` which scans the whole message
history and builds the merged filter.

### What's inherited vs what's replaced

Different constraints have different inheritance rules:

| Constraint | Rule | Example |
|---|---|---|
| `exclude_brands` / `exclude_keywords` | Union (keep adding) | Turn 1 says "不要日系", Turn 2 says "不要含酒精" → both apply |
| `price_max` | Override | Turn 1 says "300以下", Turn 3 says "200以下" → 200 wins |
| `category` | Inherit unless explicit topic change | Turn 1 is 洗面奶, Turn 2 "便宜点的呢" → still 洗面奶 |
| `brand_include` | Inherit if related, reset on topic switch | (this is where the bug lives — see below) |

### Worked example

```
Turn 1: "推荐一款洗面奶不要日系"
  → filter = { category: 美妆护肤, sub_category: 洗面奶,
               exclude_brands: [Shiseido, SK-II, …] }

Turn 2: "300以下的呢"
  → New constraint extracted: price_max=300
  → Merged with previous filter:
     { category: 美妆护肤, sub_category: 洗面奶,
       exclude_brands: [Shiseido, SK-II, …],
       price_max: 300 }
  → Retrieval pulls 洗面奶 under 300 yuan, no Japanese brands.

Turn 3: "再便宜点的呢"
  → Contextual rewriter turns this into "更便宜的洗面奶"
  → No new explicit constraints, but the previous filter still applies
  → Retrieval honors price_max=300 + no Japanese, just re-sorts by price.
```

The key: filters PERSIST across turns. The user never has to repeat
"不要日系" — they said it once and it sticks.

### The honest part — there's a known bug

When the user TOPIC-SWITCHES mid-conversation, the current
implementation gets confused. Example:

```
Turn 1: "推荐 iPad"  → returns Apple iPads, filter inherits brand=Apple
Turn 2: "推荐一款洗面奶"  → user clearly topic-switched
```

Today, the filter still carries `brand=Apple` and `sub_category=数码电子`
into turn 2. Retrieval narrows to "Apple-branded skincare" — an empty
set. The user sees "暂未收录护肤品" even though we have dozens of face
washes in the catalog.

Sam (李雨晟) identified this last night in
`docs/CONTEXT_CONTAMINATION_DIAGNOSIS.md` (on the `main` branch). He
tried five incremental patches (R8.F.4 through R8.F.8.1) and concluded
the root cause is in `sub_categories` inheritance, not any of the things
he patched. The honest fix is one of:

- **Option B**: Unified topic-switch reset — detect that the user's new
  query is from a different category and reset all category-bound state.
- **Option C**: Switch to a white-list flip — inherit nothing by default,
  carry forward only constraints the user explicitly re-affirms.

We're picking B for now (lighter change, demo-safe), with C slated for
post-defense cleanup.

### Why "exclude" stays even on topic-switch

You might ask: shouldn't the topic-switch reset clear "不要日系" too?

We chose to keep exclusions persistent ACROSS topic switches, because
the user's mental model is "I don't want Japanese stuff, period". If
they switch from face wash to headphones, they probably still don't want
Japanese headphones. We added a special clear command — "国别不限" — to
explicitly opt out of the exclusion if needed.

## Where to dig deeper

- `server/app/services/constraint_state.py` — the merge logic.
- `rag/retrieve/constraints.py` — extracts new constraints from each
  turn's text.
- `rag/retrieve/rewrite.py` — contextual query rewriting for vague
  follow-ups.
- `docs/CONTEXT_CONTAMINATION_DIAGNOSIS.md` — Sam's diagnostic of the
  multi-turn topic-switch bug, including the 4 options for fixing it.
- [`03-understanding-language.md`](03-understanding-language.md) — how
  per-turn constraints are extracted.
- `server/tests/test_context_contamination.py` — the regression suite
  Sam wrote for this bug.
