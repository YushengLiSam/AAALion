# 12 — How we measure quality

## What is this?

Building a shopping AI is easy. Building one that's RIGHT is hard.
Whenever we change anything in retrieval (the part that picks which
products to recommend), we need to know: did we make it better or
worse? This file explains the measurement framework — what tests we
run, what numbers come out, and what each number means.

## Why does it matter?

Without measurement:

- A clever-looking change might silently break a class of queries.
- We can't honestly say "this is good" in a defense panel.
- Improvements are anecdotal ("seems faster!") rather than provable.

With measurement:

- Every change is evaluated against a fixed test set BEFORE it merges.
- We have numbers to put in a slide deck.
- We can argue "this is good, here's the evidence" instead of "trust us".

## How we built it

### The golden set

The core of our evaluation is a hand-curated test set called the
**golden set**. It lives at `rag/eval/golden.jsonl` and contains 59
cases (in JSON Lines format — one JSON object per line). Each case
has the same shape:

```json
{
  "query": "推荐一款适合油皮的洗面奶",
  "category": "美妆护肤",
  "expected_product_ids": ["p_beauty_011", "p_beauty_015"],
  "forbidden_product_ids": [],
  "tags": ["basic", "skincare"]
}
```

- `query` is what the user types.
- `expected_product_ids` is the set of products that SHOULD appear in
  the top results. If our retrieval returns at least one of these in
  the top-k, that's a hit.
- `forbidden_product_ids` is the set that should NEVER appear. If any
  of these show up, that's a fail. Used heavily for negation tests
  ("不要日系" → forbidden = JP products).
- `tags` group cases by scenario (basic / negation / multi-turn /
  no-match / brand-origin / currency / multi-image, etc.).

The 59 cases cover 49 positive (with expected IDs), 10 "no-match"
(query should return zero hits because the catalog doesn't have it),
and a handful of special-scenario cases.

### How the eval runs

`rag/eval/core.py` reads the golden set, runs each query through the
retrieval pipeline, and computes metrics. We run this as `aaalion eval`
or `python -m rag.eval.report`. Output is an HTML dashboard
(`docs/eval_report.html`) plus a JSON summary.

### The metrics

**recall@k** — for each test case with an expected list, did our top-k
contain AT LEAST ONE of the expected IDs?

- If yes: 1 point.
- If no: 0 points.
- Divide by total cases → recall@k.

We report recall@5 and recall@10. recall@5 is the strict version; if a
user only looks at the top 5 results (the natural mobile-screen
behavior), what fraction of queries hit the right thing? Currently we
score **recall@5 = 0.880**.

**MRR (Mean Reciprocal Rank)** — same idea but more granular. For each
case, find the highest-ranked expected ID. If it's in position 1, score
1.0. Position 2 → 0.5. Position 3 → 0.33. And so on. Average across
all cases.

MRR rewards getting the right answer FIRST, not just somewhere in the
top-5. A retrieval that always ranks the best answer first scores 1.0.
A retrieval that always ranks it 5th scores 0.2. We currently score
**MRR = 0.828**, which means on average the best expected answer is in
position 1.2-ish.

**negation_accuracy** — for cases with a `forbidden_product_ids` list
(typically the "不要日系" tests), did the top-5 contain ZERO of the
forbidden IDs? If yes, perfect score. If any forbidden ID slipped in,
zero. We currently score **negation_accuracy = 1.000** — no forbidden
IDs ever leak through.

**no_match_correctness** — for cases tagged "no-match" (queries the
catalog can't satisfy), did retrieval return an empty or
sufficiently-low-confidence result, or did it return junk? We score
each as pass/fail.

### Beyond retrieval — end-to-end timing

We also log per-request latency in three buckets:

- `retrieval_ms` — how long retrieval took.
- `first_delta_ms` — time from request received to first token streamed.
  This is the "perceived" latency, what the user actually feels.
- `total_ms` — full duration including LLM finish and product cards.

These get printed as structured JSON in the backend log:

```json
{"event":"chat_stream","cache":"miss","retrieval_ms":317,
 "first_delta_ms":2808,"total_ms":7506,
 "query_preview":"推荐一款适合油皮的洗面奶"}
```

`retrieval_ms` is what our retrieval pipeline costs (about 200-3000 ms
depending on whether reranking runs). `first_delta_ms - retrieval_ms`
is what the LLM costs to start responding. `total_ms - first_delta_ms`
is the streaming duration.

### Stress testing

In Round 8, we ran `tools/stress_test.py` — fires 20 concurrent worker
threads at the backend for 45 seconds. Result: 100% success, ~1.9
requests/second (LLM-bound), first-delta p50 = 2.3 seconds.

That's enough to prove the backend doesn't fall over under modest load.
For real production we'd want hundreds of concurrent users; we tested
to demonstrate engineering basics, not Tencent-scale traffic.

### Catalog audits

Independent of the retrieval metrics, we periodically audit the catalog
itself. After Sam noticed our brand-origin dictionary had errors
(Nestlé → CH, not JP), we did a full audit of every product. After
Tujie noticed 19 cases in the golden set had incorrect labels, he
re-checked every label and corrected them.

These audits show up in `docs/EVAL_RESULTS.md` and the per-commit
records under `docs/commits/`.

## Honest limits

- 59 cases is small. A real production eval would have hundreds. We're
  bounded by the size of our catalog (~145 products).
- The eval set was written by us. It reflects our intuition about what
  users will ask, which is biased.
- All the metrics evaluate retrieval. They DON'T evaluate the LLM's
  text quality (we're trusting the LLM to write good replies given the
  right products). That's a separate, harder problem.
- We don't evaluate the multi-turn topic-switch bug yet because Sam
  just identified it last night; the regression tests for it are in
  `server/tests/test_context_contamination.py` but they're not in the
  golden set proper.

## Where to dig deeper

- `rag/eval/golden.jsonl` — the actual test cases.
- `rag/eval/core.py` — the eval runner.
- `rag/eval/report.py` — generates the HTML dashboard.
- `docs/EVAL_RESULTS.md` — methodology + current numbers, engineer-style.
- [`13-what-numbers-mean.md`](13-what-numbers-mean.md) — translates the
  numbers into plain English ("0.88 recall means 88 out of 100…").
- `tools/stress_test.py` — the load test.
