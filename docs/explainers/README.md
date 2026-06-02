# 狮选 LionPick — Explainers (presentation material)

> A plain-English tour of every major design choice in this project.
> No prior AI or computer-science knowledge required. If you've taken a
> first programming class, you can read everything here.

This folder is the answer to "what did you actually build, and why?" —
written for people who aren't engineers on the project. If you've ever
been handed a 200-page system-design doc and given up after page 3, this
is the opposite of that.

The technical docs (`docs/ARCHITECTURE.md`, `docs/PIPELINE.md`,
`docs/EVAL_RESULTS.md`) stay where they were — those are for engineers
adding features. **These explainers are for everyone else**: classmates,
friends, faculty advisors, defense judges, and ourselves a year from now
when we've forgotten what the code does.

---

## Who is this for?

| You are… | Start with |
|---|---|
| A CS sophomore curious about AI | `01-what-is-rag.md`, then read in order |
| A friend who has never coded | `01-what-is-rag.md` and `10-app-architecture.md` |
| A defense judge | `13-what-numbers-mean.md` → `14-honest-tradeoffs.md` → `02-finding-products.md` |
| A teammate joining today | `10-app-architecture.md` → `11-deploying-to-iphone.md` → then the others |
| Future-you in six months | start from #10 and work backward as memory dictates |

---

## Table of contents

1. [`01-what-is-rag.md`](01-what-is-rag.md) — RAG basics. Why we don't just use ChatGPT.
2. [`02-finding-products.md`](02-finding-products.md) — How we find the right products: hybrid search + reranker.
3. [`03-understanding-language.md`](03-understanding-language.md) — Understanding what the user really meant: negation + brand-origin.
4. [`04-multi-turn-conversation.md`](04-multi-turn-conversation.md) — How "再便宜点的呢" remembers what we were just talking about.
5. [`05-shopping-by-photo.md`](05-shopping-by-photo.md) — Taking a photo to find a product. CLIP, explained.
6. [`06-currency-and-prices.md`](06-currency-and-prices.md) — Showing foreign prices in RMB without lying.
7. [`07-streaming-replies.md`](07-streaming-replies.md) — Why the answer types itself out word by word.
8. [`08-cache-and-speed.md`](08-cache-and-speed.md) — Making it fast: cache, prewarm, async-offload.
9. [`09-voice-and-tts.md`](09-voice-and-tts.md) — Voice input (you speak) and TTS (it speaks back).
10. [`10-app-architecture.md`](10-app-architecture.md) — The whole system in one picture.
11. [`11-deploying-to-iphone.md`](11-deploying-to-iphone.md) — Getting the app onto your phone over the public internet.
12. [`12-how-we-measure.md`](12-how-we-measure.md) — Measuring quality: golden sets, recall@5, MRR.
13. [`13-what-numbers-mean.md`](13-what-numbers-mean.md) — Our current scores in plain English.
14. [`14-honest-tradeoffs.md`](14-honest-tradeoffs.md) — Everything we chose NOT to do and why.
15. [`15-repurchase-reminders.md`](15-repurchase-reminders.md) — Reminding you to restock things you've bought.

---

## How to read each file

Every explainer follows the same four-section template:

1. **What is this?** — One or two sentences in plain English.
2. **Why does it matter?** — A concrete user problem.
3. **How we built it** — A walk through the actual code with file paths.
4. **Where to dig deeper** — Links to the engineer-facing docs and commits.

You can stop at any section. Section 1 alone is enough to follow a
conversation about the feature. Section 3 is where the engineering lives.

---

## Things we won't assume you know

Each file defines technical terms the first time it uses them. If a file
ever uses a word you don't recognize, that's a documentation bug — tell
Shufeng and it'll get fixed. We promise.

---

## A note on honesty

These docs describe what's actually built. When something is half-built or
known-broken, we say so. The goal is to be defendable, not to look polished.
[`14-honest-tradeoffs.md`](14-honest-tradeoffs.md) is the catalog of things
we explicitly decided NOT to build.

---

## Who wrote this and when

Shufeng Chen, 2026-05-27, after Round 9 of the project. The technical depth
behind these explainers is in `docs/commits/` (per-commit records) and
`docs/PLAN_ARCHIVE.md` (historical plan). If a fact here looks stale,
those are the sources of truth.
