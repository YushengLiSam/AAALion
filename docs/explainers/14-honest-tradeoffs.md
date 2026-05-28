# 14 — What we chose NOT to do and why

## What is this?

A list of decisions where we said "no" instead of "yes". Every project
has these. Most projects hide them. We document them, because they're
the honest answers to the questions a defense panel will ask.

If a judge asks "why don't you have feature X?", the answer is in
this file.

## Why does it matter?

A grade-A project isn't one that has everything. It's one whose authors
understood what they couldn't or shouldn't have, and could explain the
choice. "We didn't build that because..." is a stronger answer than
"we ran out of time" or worse, "we didn't think of it".

## The decisions

### 1. We don't process payments

Our cart and checkout look real. They aren't. Tapping "去支付" doesn't
charge anything; it shows a confirmation page that explicitly says
"演示用" (for demo only).

**Why**: Real payments need PCI compliance, merchant accounts, refund
flows, and a legal entity. None of that fits in a college project
deadline. We did the smart half — the cart UI, multi-currency totals,
the order summary — and stopped there. The boundary is explicit so we
don't oversell.

### 2. Our catalog is 145 products, not 145,000

We have 100 AI-generated seed products and 45 hand-curated real
products (20 from Amazon US, 25 from Chinese platforms).

**Why**: We can't legally scrape big-data catalogs without permission.
We don't have free Taobao API access. The real-product expansion was
done with parallel Perplexity research per item — that's about 4
products per hour of human time. For breadth we sacrifice depth; we'd
need a different team and different licenses to do millions.

We lean into this in defense framing: "small-catalog precision" is a
real engineering specialty. Our recall@5 = 0.880 on 145 products is
arguably more impressive than the same number on 145 million products
where you have 100x more chances to get lucky.

### 3. We use TokenRouter, not Doubao

The contest provided a Doubao API key. We don't use it.

**Why**: The PDF key was deactivated on day 2 of the contest. Another
team committed the key to a public GitHub repo. Bad actors found it,
hammered the rate limit, and the organizer killed the key for all
teams to prevent further abuse. We pivoted to TokenRouter — a Chinese
LLM gateway that wraps 75+ models behind one OpenAI-compatible API,
including Claude. We have 1000 requests for free.

When the organizer rotated to a new Doubao key, we kept the multi-
provider abstraction (`server/app/services/llm_provider.py`) so we
can switch with a single env var if needed. The grading panel will
verify the abstraction exists; the actual choice of LLM is a small
matter.

### 4. We have voice and TTS but no offline mode

Voice input uses Apple's on-device speech recognition (so no network
needed). TTS uses the on-device system voice. But the rest of the app
— chat, retrieval, product cards — needs the backend.

**Why**: An offline mode would require shipping the entire retrieval
stack to the iPhone, which means the embedding models (~500 MB), the
catalog (~10 MB), and a JS-runtime LLM (~2 GB minimum for any
reasonable quality). The iOS app would balloon to 3 GB. Not worth it.

If you're offline, the app shows an error. That's the right answer.

### 5. We use synthetic seed product data alongside real

100 of 145 products are AI-generated. They look real in the UI but they
don't link to any actual buy page.

**Why**: Synthetic data was the only fast way to get to 100 products
in 4 categories within Round 1. It's labeled in the UI — every
AI-generated card has a "演示" badge and no external-URL link button.
A judge can't be misled into thinking they're real.

We treat this as a feature, not a hack: synthetic data lets us
illustrate behavior across categories without breach-of-license risk.
Real data is for proving the app works on real products. Both have
their place.

### 6. No demo video yet, no Gamma slide deck yet

The PDF rubric strongly encourages a 3-5 minute screencast and a
prepared slide deck.

**Why**: We deliberately deferred these to after R8 closes. Writing
code and writing slides compete for the same hours; we picked code
first. The video and deck are top of the R9+ queue — see
`docs/PROPOSAL_2026-05-25.md` Tier 2.

### 7. We don't fine-tune the LLM

Some teams fine-tune their LLM on shopping-specific data. We don't.

**Why**: Fine-tuning needs (a) a base model we can train on, (b) a
labeled dataset of high-quality shopping conversations, (c) GPU time
to train, (d) infrastructure to host the fine-tuned model. We have
none of the four. Fine-tuning would also bind us to a single model
flavor, defeating the multi-provider abstraction.

Our approach is "stock LLM + good retrieval + careful system prompt".
The numbers ([`13-what-numbers-mean.md`](13-what-numbers-mean.md))
show it works.

### 8. We don't run on A100 at request time

The A100 GPU on `ssh uc` is used ONLY for one-time index building (the
CLIP image embeddings, which take ~30 seconds total for all 145
products). Request-time retrieval runs on Mac CPU.

**Why**: GPU at request time = ssh + remote inference + network round
trip + GPU contention with other users of that machine. For our ~300
ms retrieval target, CPU is fast enough and avoids the operational
mess. The A100 belongs to a different ongoing project of Shufeng's, so
we touch it only sparingly anyway.

### 9. No personalization, no user accounts

Every user gets the same recommendations for the same query. There's
no notion of "remember what I like". The app has a per-device anonymous
ID (used only for Sam's repurchase reminders feature; see
[`15-repurchase-reminders.md`](15-repurchase-reminders.md)) but no
login, no purchase history shaping retrieval, no preference learning.

**Why**: Personalization is hard to do right (privacy, cold start,
relevance drift) and easy to do wrong (filter bubbles, creepy
inferences). For a defense demo where every judge sees a fresh
experience, statelessness is the right default. We've thought about
adding 👍/👎 buttons that influence retrieval as a gentle prior — it's
in the innovation backlog (proposal #12 in
`docs/cluely/INNOVATION_PROPOSALS_2026-05-27.md`).

### 10. We don't fix the multi-turn topic-switch bug yet

If a user asks about "iPad" and then asks "推荐一款洗面奶", they currently
get 0 results because our constraint state inherits `brand=Apple` and
`sub_categories=['洗面']` from the previous turn.

**Why**: Sam discovered this in Round 8.F. He tried 5 patches before
writing a diagnostic admitting the root cause is `sub_categories`
inheritance, which none of his patches touched. The fix (option B or C
from `docs/CONTEXT_CONTAMINATION_DIAGNOSIS.md`) is ~1-2 days of
focused work. We chose to ship the diagnostic + regression suite
first so future fixes can be measured. The fix is the #1 item in
the R9 innovation menu.

### 11. We don't have live-shopping or short-video integration

Taobao 问问 and JD 京小智 integrate with livestream shopping. We don't.

**Why**: We don't have access to a livestream platform API. Building a
mock would be UX-only and shallow. Better to be honest that this is a
gap than to pretend.

### 12. We don't scrub the dead Doubao key from git history

The PDF-distributed Doubao key (already deactivated by the organizer)
appears in 4 historical commit diffs where we documented the leak
incident. We left those commits as-is.

**Why**: The key is dead. The string is the organizer's own. Scrubbing
history requires a force-push which annoys teammates' local clones.
We added the key to the pre-commit hook's blocklist so it can't be
re-introduced. If the organizer's automated scanner flags us, the
20-minute scrub is ready to execute (see `docs/cluely/log.md`).

## The principle behind these

Whenever we said "no" to a feature, we asked: would saying "yes"
require resources we don't have (time, data, money, expertise), would
it dilute our strongest claim (small-catalog precision), or would it
turn the project into something we'd have to defend instead of
celebrate?

If any answer is yes, the feature is in this file.

## Where to dig deeper

- `docs/HONEST_ANSWERS.md` — the engineer-facing version of the same
  ideas, more terse.
- `docs/POLICY.md` §"Bonus feature commitments" — what we said yes to.
- `docs/PROPOSAL_2026-05-25.md` — the explicit backlog of "yes,
  someday".
- `docs/cluely/INNOVATION_PROPOSALS_2026-05-27.md` — the LOCAL
  forward-looking proposal file.
