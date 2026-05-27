# Multi-turn Context Contamination — Root Cause Diagnosis

> Phase 0-2 of the diagnostic plan. **No business code was modified to
> produce this document** — only `server/tests/test_context_contamination.py`
> was added to reproduce the symptoms reliably. Phase 3 (option selection)
> is gated on user choice; Phase 4 implements it.

## TL;DR

**Multi-turn state inheritance is a black-list-style pattern with
per-dimension reset rules, and the sub_categories dimension has the
leakiest behaviour.** The patches I shipped over the last hour
(R8.F.4 / R8.F.6 / R8.F.7 / R8.F.8 / R8.F.8.1) addressed category and
brand contamination, but left **sub_categories untouched**. They
inherited from turn 1's "洁面" keyword forever and bypassed every
patch I added because no patch looked at sub_categories.

Concretely: 5-turn conversation `[洁面 → iPad → 鞋子 → 纸尿片 → 护肤品]`
ends up with `inherited.sub_categories=['洁面']` on the final turn, so
querying for 护肤品 retrieves **1** product (only 珊珂洗颜 happens to
have sub_category=洁面 in 美妆护肤) instead of all 35.

## What Phase 0 reproduced

`server/tests/test_context_contamination.py` produces these failures
with the current code:

| Test | Layer | Result |
|---|---|---|
| `CaseA inherited_state` | filter | **pass** — print shows `category='美妆护肤'` after [洁面 → iPhone 12], the inherited category did NOT update to None when the last turn was about iPhone |
| `CaseA no_skincare_in_iphone_results` | e2e | **pass via Path A** — anchor `iphone` in raw query forces `conversation_filter = None`, so retrieval recovers despite contaminated inherited state |
| `CaseB inherited_state` | filter | **pass** — `category='美妆护肤'` (extracted from last "护肤品"), `brand_include=None`. The brand-contamination hypothesis from the analysis was incorrect for this catalog. |
| `CaseB skincare_results_not_blocked_by_apple_brand` | e2e | **pass** — but only because Path B did nothing here (no brand to drop), not because the system is correct |
| `CaseC each_turn_reflects_current_query_only` | e2e | **FAIL** — 5-turn cumulative pollution. Last turn `护肤品` returns just `['珊珂洗颜洁面乳']` because `sub_categories=['洁面']` inherited from turn 1 narrows 美妆护肤 catalog to 1 row |
| `InvariantLegitimateInheritance.test_followup_after_iphone_keeps_iphone` | filter | **FAIL** — `推荐 iPhone → 再便宜点` returns `inherited.category=None`. The system doesn't recognize iPhone as a category-bearer (it's an SKU line, not in `_INFERRED_CATEGORIES` for 数码电子), so even legitimate same-topic follow-ups break. |

The failing test was the one the user actually observed: after several
topic switches in the simulator, `护肤品` returned a near-empty catalog.

## Per-dimension inheritance audit (Phase 1)

`server/app/services/constraint_state.py:_merge_turn` is the
authoritative single-pass merge across all messages. The default for
every dimension is **"new turn has no signal → keep prior value"**.
Specific reset triggers per dimension:

| Dim | New-signal path | Explicit-clear path | Other reset |
|---|---|---|---|
| `category` | overrides on new turn category | `品类不限` clears | none |
| `sub_category` | overrides when turn has sub_category | `品类不限` clears | **resets when category strictly changes AND new turn has no sub-info** |
| `sub_categories` | overrides when turn has sub_categories | `品类不限` clears | **resets when category strictly changes AND new turn has no sub-info** |
| `brand_include` | full replacement on new brand mention | `品牌不限` clears | none |
| `brand_exclude` | unioned with new exclusion | `品牌不限` clears | none |
| `exclude_keywords` | unioned with new exclusion | `国别不限` clears | none |
| `price_min_cny` / `price_max_cny` | new turn's value overrides | `预算不限` clears | none |

The "category strictly changes" clause (lines 71-79 of constraint_state.py)
is the ONLY reset for sub-info that doesn't require an explicit
"品类不限" Chinese phrase. It fires only when:

1. New turn produces an extractable category, AND
2. That category differs from the inherited one, AND
3. New turn has no sub-info of its own.

The 5-turn pollution slips through because:

* `推荐 iPad` — `build_retrieval_filter("推荐 iPad")` returns `None`
  (no map entry for "iPad" as a generic word). `_merge_turn` returns
  early at line 68-69. **State unchanged. sub_categories=['洁面'] sticks.**
* `我想要 鞋子` — same: `build_retrieval_filter` returns `None`. State unchanged.
* `纸尿片` — same: no map entry. State unchanged.
* `护肤品` — DOES match `_DIRECT_CATEGORIES`. Produces `category=美妆护肤`.
  Inherited category is also 美妆护肤. `category_changed = False`. The
  reset clause does NOT fire. sub_categories=['洁面'] survives → catalog
  narrows to 1 product.

## Layered patches I shipped this evening (and why they didn't help)

`rag_client.top_k` has my Path A + Path B detection (R8.F.7 / 8 / 8.1).
These run AFTER `build_conversation_filter` has already produced the
polluted inherited state. They check two things:

* **Path A**: anchor word in raw query (`iphone` / `ipad` / `macbook` / ...).
  If yes, `conversation_filter = None` + use raw text for retrieval.
* **Path B**: category / brand conflict between raw query's extracted
  filter and the inherited filter. If yes, same reset.

What Path A/B do NOT check:

* **sub_categories conflict.** The 护肤品 case: inherited.category =
  raw.category (both 美妆护肤) → Path B sees no conflict → no reset.
  But inherited.sub_categories=['洁面'] is still pinned, and the
  retrieval filter inherits it.
* **price_max_cny / price_min_cny conflicts.**
* **exclude_keywords conflicts.**

So my patches only covered category and brand. **Three out of six
dimensions have NO reset coverage at all** in the rag_client layer,
and `_merge_turn` only resets sub_categories under one narrow
condition (category strictly changed).

## Root cause: black-list inheritance, multiple dimensions, each with its own reset rule

Three structural choices compound to produce this:

1. **Default is to inherit, not to reset.** Every dimension's default
   is "no new signal → keep prior." Without an explicit reset trigger,
   sticky state never goes away. This is a pure black-list pattern —
   everything inherits, you only fight the leaks reactively.

2. **Each dimension has its own reset trigger.** category needs
   `品类不限` or a different category extracted. sub_categories needs
   category to strictly change (NOT "category re-confirmed at the
   same value"). brand_include needs `品牌不限` or full replacement.
   Six dimensions, six different reset rules, no shared "topic
   switch resets everything" gate.

3. **Reset triggers depend on keyword tables that aren't complete.**
   `_DIRECT_CATEGORIES`, `_INFERRED_CATEGORIES`, `_INFERRED_SUB_CATEGORIES`,
   `TOPIC_SWITCH_HINTS` all encode "which Chinese keywords map to what."
   Coverage is partial by definition (no list can be exhaustive). Every
   gap in coverage is a contamination vector.

These three together explain why I've spent the last hour adding
keywords to one table and conflict checks to one path, only to find
another dimension (sub_categories) untouched by every patch.

## Why patches haven't worked

* **R8.F.7** (Apple anchor reset) handled `category=美妆 → query=iPhone`
  — but only the anchor case, and only by resetting category. Did
  nothing for sub_categories.
* **R8.F.8** (TOPIC_SWITCH_HINTS table) extended the keyword coverage
  for 食品生活/服饰运动 etc. — but the dimension being polluted in
  CaseC isn't category, it's sub_categories. The hint table only feeds
  Path B's category comparison; it doesn't reach `_merge_turn`'s
  sub_categories logic.
* **R8.F.8.1** (brand-conflict + raw-cat-without-inh-cat) — closer
  to what was needed, but still only inspects category and brand_include.
* None of them touched `_merge_turn`, which is where sub_categories
  actually lives.

The pattern is: every patch fixes ONE more symptom by widening ONE
more rule. Every time I think I'm done, the user finds a query that
slips through a different dimension or a different table. This is
exactly the whack-a-mole shape the plan warned about.

## Answers to Phase 2's required questions

**Q1. Which dimensions get contaminated?**
All six (category, sub_category, sub_categories, brand_include,
brand_exclude, exclude_keywords, price). In practice the loudest
one in this codebase is **sub_categories**, because the reset
clause requires the category to STRICTLY change — and the most
common contamination scenarios either repeat the category or have
no extractable category at all.

**Q2. What does the reset logic depend on?**
Three keyword tables (`_DIRECT_CATEGORIES`, `_INFERRED_CATEGORIES`,
`_INFERRED_SUB_CATEGORIES`) plus the topic-only hint table I added.
Coverage is necessarily partial. Both `_merge_turn` and the
rag_client Path A/B depend on these tables firing the right rules.

**Q3. Black list or white list?**
**Pure black list.** Every dimension defaults to "inherit." Resets
fire only on explicit cancellation phrases ("品类不限") or on
detected conflicts whose detection depends on the same partial
keyword tables. There is no global "user changed topic → blow away
all inheritance" gate. White-list would be the opposite: nothing
inherits unless the new query is recognized as a follow-up.

**Q4. Is the patch route treatable or fundamental?**
**Fundamental.** As long as the default is "inherit everything" and
each dimension has its own incomplete trigger, every new query
shape is a chance for a new leak. The recent five-commit cycle is
evidence: every patch widened a rule, and each time the user
typed a slightly different query, a different dimension leaked.

---

# Phase 3 — Options for the user to pick

(I am stopping here per the plan and listing options for you to choose
before any code goes in.)

## Option A — Add a SECOND dimension to Path A/B (just fix sub_categories now)

Smallest possible change. In `rag_client.top_k`'s Path B, also check
inherited.sub_categories vs raw text. Add a small fresh-extraction of
sub_categories from raw_message. If conflict, also drop sub_categories.

* **Pros**: ~10 lines, contained, fixes the specific failing case I
  just diagnosed.
* **Cons**: This is more whack-a-mole — exactly what the plan said
  not to do. There are still three more dimensions (price,
  exclude_keywords) with no Path A/B coverage. The next user query
  that pollutes one of those will need another patch.
* **Defensibility**: low. If the interviewer / Codex reads the diff
  they'll see we kept adding rules.

## Option B — Single "topic-switch resets EVERYTHING" gate, signal-driven

In rag_client.top_k, define ONE function:
`is_topic_switch(raw_query, inherited_filter) -> bool`.
If true, throw away **the entire inherited filter** (all six
dimensions at once), not just category. The detection signal can be
the same Path A/B keywords I already have.

* **Pros**: ~20 lines, kills five known leaks in one stroke
  (sub_categories + price + exclude_keywords once they're under the
  same gate). Conceptually clean: "if user changed topic, none of
  the old turn's structured state should survive."
* **Cons**: Still requires a topic-switch SIGNAL (anchors / hint
  table). Same keyword-coverage problem as before — just consolidated
  into one place rather than spread across dimensions.
* **Defensibility**: medium. "We have ONE topic-switch gate that
  drops everything inherited" is a much cleaner story than the
  current per-dimension patchwork.

## Option C — Flip to WHITE-LIST inheritance

Default is **don't inherit anything**. Only inherit when the new
query is a recognized FOLLOW-UP: short, no extracted category /
brand of its own, and matches a follow-up pattern like
"再便宜点的" / "颜色" / "第二个加进购物车" / "有别的吗".

* **Pros**: This is what the plan recommends as the "kill at the
  root" option. Default-deny is the right shape for context state.
  No more keyword table for "did the user change topic" — instead
  we have a smaller, more verifiable table for "did the user
  continue."
* **Cons**: Larger refactor of `_merge_turn`. Risk of breaking
  legitimate multi-turn that depends on inheritance (price refinement,
  "再来一个 iPad Pro 但要 256GB", etc.). Needs careful regression
  testing. Maybe a day's work.
* **Defensibility**: high. "We flipped the default from inherit-all
  to inherit-only-when-explicitly-a-follow-up" is the kind of design
  decision you can walk an interviewer through.

## Option D — One-LLM-call classifier per turn

Every turn, run a small LLM call: "is this a follow-up to prior
context, or a new topic?" If new topic → drop all inherited state.
If follow-up → keep.

* **Pros**: Most general, no keyword tables. The LLM understands
  edge cases we can't enumerate.
* **Cons**: One extra LLM call per turn — adds 0.5-2s latency and
  per-request cost. Caching helps but doesn't fully eliminate.
  Tujie's existing design specifically avoided LLM-in-the-hot-path
  for this exact reason (the chat path is already slow).
* **Defensibility**: medium-high. "We use an LLM to decide" is
  defensible but also invites the obvious "why didn't you do this
  earlier?" question.

## My honest take

* **A** is the trap the plan warned us about. Skip.
* **D** is overkill for the demo timeline and trades off latency
  we already worked hard to bring down.
* **Between B and C**, B is the smallest principled fix; C is the
  cleanest design.

I lean toward **B for tonight (demo-stable), with a follow-up commit
clearly slating C as the proper structural fix** — same way Tujie
documented the 1.15-MP Anthropic image cap as a follow-up.

But this is a `[人工]` decision per the plan. **Please choose A / B /
C / D / something else** before I write any code in Phase 4.
