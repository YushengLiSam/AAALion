# 03 — Understanding what the user really meant

## What is this?

People don't ask in clean keywords. They say "推荐防晒霜，不要日系，预算 300
以内" (recommend sunscreen, no Japanese brands, budget within 300 yuan).
A naive search would treat every word equally and probably return a
Japanese sunscreen costing 500. Three things need to happen:

1. **Pull out the intent** — what's the user actually looking for ("sunscreen")
   vs the constraints ("not Japanese", "≤300 yuan").
2. **Rewrite vague queries** — "再便宜点的呢" (something cheaper) doesn't make
   sense out of context.
3. **Apply hard filters** — anything explicitly excluded must be filtered
   out before the LLM ever sees it.

We do all three. They live in `rag/retrieve/`.

## Why does it matter?

If we just shove the user's raw text into the search engine:

- "**不要日系**" becomes search terms "不要" and "日系". The engine sees "日系"
  as a positive signal and ranks Japanese products HIGHER. The literal
  opposite of what the user wants.
- "**便宜点的**" has no information by itself — "cheaper than what?" — so the
  engine returns random cheap things.

These aren't edge cases. They're the most common shape of conversational
shopping queries.

## How we built it

### Part A — Negation extraction

The negation extractor (`rag/retrieve/negation.py`) scans the user's
message for patterns like:

- `不要 X` (don't want X)
- `除了 X` (except X)
- `不含 X` (without X)
- `没有 X` (no X)

When it finds one, X gets added to a **forbidden list** — these things
should NEVER appear in the results. The forbidden list is a structured
object with three buckets:

```
{
  "exclude_brands":    ["Shiseido", "SK-II"],   # excluded by brand
  "exclude_keywords":  ["日系", "酒精"],         # excluded by keyword
  "exclude_categories": [],                     # excluded by category
}
```

Then in retrieval, after we get the candidate products, we drop any
product where the brand is in `exclude_brands` or the description
contains an excluded keyword. The filter happens BEFORE the LLM sees
anything, so the LLM literally never has a chance to recommend a Japanese
brand if "不要日系" was said.

### Part B — Brand-origin mapping

"不要日系" is harder than "不要资生堂" because "日系" isn't a brand name —
it's a country origin. We need to know that Shiseido is Japanese, SK-II
is Japanese, La Mer is American, etc.

For this we built a dictionary mapping ~70 brands to their ISO-2 country
codes (`rag/retrieve/brand_origin.py`):

```python
BRAND_ORIGIN = {
    "Shiseido": "JP",  "SK-II": "JP",  "Hada Labo": "JP",
    "Estée Lauder": "US",  "Lancôme": "FR",  "La Roche-Posay": "FR",
    "Beiersdorf": "DE",  "Boots": "GB",
    "Innisfree": "KR",  "AHC": "KR",  "Sulwhasoo": "KR",
    # ...
}
```

When the user says "不要日系", the negation step finds that "日系" → country
"JP" → looks up all JP brands and adds them to `exclude_brands`. Same for
"韩系" (KR), "法系" (FR), "美系" (US), "德系" (DE), "英系" (GB).

This mapping was hand-built and audited. We've checked it against the
catalog, so when judges run "不要日系" they actually get no Japanese
results.

### Part C — Contextual query rewriting

For vague follow-ups like "再便宜点的呢" (something cheaper), the raw text
has no useful keywords. We attach **context from the previous turn** by
calling a small LLM that rewrites the follow-up into a self-contained
query.

Example:

```
Turn 1: 推荐一款洗面奶
Turn 2: 再便宜点的呢

→ Rewrite turn 2 to: 推荐一款便宜的洗面奶
```

The rewrite happens in `rag/retrieve/rewrite.py`. We only do it when the
follow-up looks vague (heuristic: no nouns, contains comparator words
like "便宜点 / 大点 / 更 X"). For self-contained queries, we skip the
rewrite — saves an LLM call.

### Part D — Stateful filters

Even with rewriting, some constraints need to persist across turns. If
the user said "不要日系" in turn 1 and asks "再便宜点的呢" in turn 2, we
shouldn't lose the "no Japanese" constraint. The stateful-constraint
service (`server/app/services/constraint_state.py`) keeps a rolling
filter that inherits from previous turns and adds new constraints from
the current turn. See [`04-multi-turn-conversation.md`](04-multi-turn-conversation.md)
for the full multi-turn story.

## A concrete example

User says: "推荐防晒霜不要日系不要含酒精"

1. **Negation extraction** finds two `不要` patterns:
   - "不要日系" → `exclude_brands = [Shiseido, SK-II, Hada Labo, …]` (via brand-origin lookup)
   - "不要含酒精" → `exclude_keywords = ["酒精"]`
2. **Retrieval** runs as normal (BM25 + dense + rerank, see
   [`02-finding-products.md`](02-finding-products.md)).
3. **Filter pass** drops every candidate product whose brand is in
   `exclude_brands` OR whose description contains "酒精".
4. **LLM** receives only the surviving products. It generates a reply
   that mentions only those.

End result: 理肤泉 (法国) and 巴黎欧莱雅 (法国) get recommended; Shiseido
and SK-II are quietly removed. The LLM doesn't even know they existed
for this query.

## Where to dig deeper

- `rag/retrieve/negation.py` — the regex + LLM-fallback that extracts
  forbidden lists.
- `rag/retrieve/brand_origin.py` — the brand→country dictionary plus the
  reverse lookup (country keyword → brand list).
- `rag/retrieve/rewrite.py` — the contextual query rewriter.
- [`04-multi-turn-conversation.md`](04-multi-turn-conversation.md) — how
  filters persist across turns.
- `docs/EVAL_RESULTS.md` — measured `negation_accuracy = 1.000` across
  the audited test cases.
