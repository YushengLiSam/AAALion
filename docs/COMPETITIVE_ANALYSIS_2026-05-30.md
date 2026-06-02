# 狮选 LionPick — Honest Competitive Analysis (2026-05-30)

> Where our product actually stands vs the market, grounded in web
> research (sources at the bottom). Written to be honest, not flattering
> — a judge will know the incumbents, so overclaiming loses more points
> than it gains. "We" = 狮选 LionPick at `6b57d2d`.

---

## 1. The landscape in one paragraph

By mid-2026 the market converged on one pattern: **conversational LLM
front-end → multimodal product retrieval → agentic checkout**, split into
two philosophies. **Alibaba (千问+淘宝, live 2026-05-11)** ships the
"**assistant**": category-level candidate lists, human stays in the loop.
**ByteDance (豆包+抖音 "一句话购物", expanded beta from 2026-03-30)** ships
the "**agent**": specific SKUs, checkout *inside* 豆包, app boundaries
hidden — built on the **火山方舟** RAG/multimodal stack. JD (京犀) is in
beta; Pinduoduo has no mature external AI 导购 but owns the **拼单** social
mechanic. Globally, Amazon Rufus, Perplexity, Google AI Mode, OpenAI and
Microsoft all shipped agentic checkout in late 2025 — **then OpenAI pulled
back from Instant Checkout and Klarna re-hired humans**, proving execution
reliability, not discovery, is the hard part.

**The single most important fact for us:** the competition host
(ByteDance) ships the closest comparable to us (豆包 一句话购物) on a public
RAG stack (火山方舟). We are, in effect, building a focused, transparent,
open version of their own product. That framing reads as fluent to their
judges.

---

## 2. Capability matrix — us vs the field

| Capability | 狮选 (us) | 淘宝/千问 | 豆包/抖音 (ByteDance) | JD 京犀 | PDD | Rufus / Perplexity / Google |
|---|---|---|---|---|---|---|
| Text semantic search | ✅ hybrid+rerank | ✅ shipped, 4B SKU | ✅ beta | ✅ beta | partial | ✅ shipped |
| Photo / image search | ✅ CLIP image-first | ✅ 拍立淘 (mature) | ✅ via 方舟 | claimed | — | ✅ (Rufus/Snap-to-Shop) |
| Multi-turn dialogue | ✅ **+persistent negation** | ✅ (weak) | ✅ (weak) | ✅ (weak) | — | ✅ |
| Negation / exclusion | ✅ **structured, 1.000 on golden** | ⚠️ documented "答非所问" | ⚠️ context-conflation | ⚠️ "已读乱回" | — | ⚠️ varies |
| Personalization | ✅ explainable 👍/👎 prior | ✅ | ✅ (history) | ✅ (no time-decay) | ✅ (social graph) | ✅ |
| Group-buy / 拼单 | ✅ **(simulated, labelled)** | — | — | — | ✅ **core** | — |
| Agentic cart/checkout | ✅ (demo, in-app) | ✅ shipped | ✅ beta (in-app) | ✅ beta | — | ✅ (then OpenAI retreated) |
| Answer transparency | ✅ **hallucination receipts + why-card** | ❌ ad black box | ❌ ad black box | ❌ | ❌ | partial |
| Real catalog scale | ❌ ~145 demo products | 4B | Douyin catalog | JD catalog | PDD catalog | 50B+ (Google) |
| Real payment | ❌ demo checkout | ✅ | ✅ | ✅ | ✅ | ✅ |
| Real multi-user / accounts | ⚠️ seam built, cloud-pending | ✅ | ✅ (Douyin bind) | ✅ | ✅ | ✅ |

Legend: ✅ have it · ⚠️ partial/weak · ❌ don't have it.

---

## 3. Where we are genuinely competitive (lead with these)

1. **Negation / exclusion + multi-turn robustness.** This is the
   incumbents' *documented* weak spot — independent reviewers caught
   Taobao, JD and Douyin all "答非所问" / conflating context (e.g. Douyin:
   a gift for a grandmother → "适合送喜欢健康的男朋友的奶奶的养生壶"). We
   handle "不要日系" *and persist it across turns* ("再便宜点的呢" keeps the
   ban), with negation accuracy 1.000 on our golden set. **A robust
   negation/compare flow is the clearest place a judge sees real
   technical merit.** Academia agrees — there's a dedicated 2025 paper on
   negation query rewriting; it's a known-hard problem.

2. **Answer transparency.** The ad-incentive black box is unsolved
   industry-wide — neither Alibaba nor ByteDance has reconciled
   "recommendation vs. ad placement." Our **hallucination receipts**
   (每条claim标 已验证/推断) + **"why recommended" card** (shows the
   retrieval signals + preference bonus) + **non-commercial ranking** is a
   credible, demo-able differentiator the incumbents structurally can't
   match.

3. **拼单 × AI fusion (white-space).** PDD owns 拼单 but has no AI 导购; the
   AI players (淘宝/豆包/JD) have no 拼单. **Nobody fuses conversational AI
   导购 with group-buy.** Ours is simulated, but the *concept* — "AI helps
   you pick, then helps you 拼单 cheaper with friends" — is genuinely
   novel positioning, and PDD's model proves the social mechanic drives
   conversion.

4. **Eval rigor.** We have a held-out golden set (71 cases), measured
   recall@5 (~0.88–0.98 depending on subset), MRR, negation accuracy
   1.000, p50 latency ~68 ms, an anti-cherry-pick audit, and a stress
   test. Research on hackathon judging is explicit: **eval rigor is an
   increasing discriminator** for AI-agent tracks — metrics beat vibes.

5. **Architecture maturity for a student team.** Hybrid (dense+BM25) +
   cross-encoder rerank + query rewrite + structured negation filter +
   retrieval cache is exactly the textbook 2025 SOTA pipeline. Clean
   provider/cloud/identity seams. systemd + tunnel deploy. This reads as
   engineering maturity.

---

## 4. Where we are honestly behind (own these, don't hide them)

1. **Catalog scale & realism.** ~145 demo products vs 4B–50B. Our seed
   data is synthetic (we now use real product data for the demo, but the
   catalog is tiny). *Framing:* we're a vertical proof-of-concept, not a
   marketplace — judge the pipeline, not the inventory.
2. **No real payment / real multi-user yet.** Checkout is a demo path;
   group-buy is simulated; accounts work but aren't wired to a cloud user
   store. The seams are built — it's a wiring + scope decision, not a
   rewrite. *And:* even OpenAI **retreated** from agentic checkout and
   Klarna **re-hired humans** — so a scoped, reliable demo is the *correct*
   choice, not a shortfall. Lean on that.
3. **Not on Doubao right now.** We run TokenRouter haiku for latency (14 s
   → 2 s). For a ByteDance competition that's a narrative risk — be ready
   to explain it's a provider-swap behind one env var and the 方舟/Doubao
   path is wired.
4. **English path is slow** (multilingual reranker, 30 s+). Demo in
   Chinese; it's a Chinese-market product anyway.
5. **Our eval numbers are on our own golden set,** not a public benchmark
   (ESCI etc.). Don't compare our 0.98 to public 0.66 — different sets.
   Present honestly as "on our held-out set."
6. **Multimodal fine-grained attribute errors.** Plain CLIP processes
   images globally and misses fine attributes (the SOTA fix is a
   vision-LLM caption/attribute pass — see proposal). Our photo search
   demos well but will mis-rank visually-similar items.

---

## 5. The one-line positioning for the defense

> "**狮选 is a transparent, vertical AI 导购** — it does the thing
> ByteDance's own 豆包 一句话购物 does (conversational multimodal
> retrieval + agentic actions on the 方舟-style stack), but adds the two
> things the whole market is missing: **trustworthy reasoning** (it shows
> its work and never hides ads) and **social 拼单**. We don't compete on
> catalog size; we compete on **getting the answer right and proving it**
> — exactly where the incumbents are documented to fail."

---

## Sources (accessed 2026-05-30)

- 千问×淘宝 全面打通 (Sina, 2026-05-11): https://finance.sina.com.cn/roll/2026-05-11/doc-inhxpatp9122789.shtml
- 豆包"一句话购物"内测 (证券时报, 2026-03-20): https://www.stcn.com/article/detail/3687846.html ; (虎嗅): https://www.huxiu.com/article/4847869.html
- 两种AI电商路线 千问vs豆包 (钛媒体): https://www.tmtpost.com/7985764.html
- AI导购"答非所问" 淘天/京东/抖音 (澎湃): https://www.thepaper.cn/newsDetail_forward_29798462
- 京犀/京东AI购 beta (Sina, 2025-12-26): https://finance.sina.com.cn/tech/roll/2025-12-26/doc-inheayzp2764335.shtml
- 拼多多 AI 现状 (Sina, 2025-02): https://finance.sina.com.cn/stock/relnews/us/2025-02-26/doc-inemuzve7954012.shtml
- 火山方舟 (RAG/多模态/agent, upd. 2026-05): https://www.volcengine.com/docs/82379/1099455
- Amazon Rufus → Alexa for Shopping (CNBC, 2026-05-13): https://www.cnbc.com/2026/05/13/amazon-ditches-rufus-ai-chatbot-in-favor-of-alexa-shopping-agent.html
- OpenAI retreats from Instant Checkout (CNBC, 2026-03-24): https://www.cnbc.com/2026/03/24/openai-revamps-shopping-experience-in-chatgpt-after-instant-checkout.html
- Klarna re-hires humans (CX Dive): https://www.customerexperiencedive.com/news/klarna-reinvests-human-talent-customer-service-AI-chatbot/747586/
- Google AI Mode + agentic checkout (blog.google, 2025-11-13): https://blog.google/products-and-platforms/products/shopping/agentic-checkout-holiday-ai-shopping/
- Learning to Rewrite Negation Queries (COLING 2025): https://aclanthology.org/2025.coling-industry.49.pdf
- bge-reranker-v2-m3 (HF): https://huggingface.co/BAAI/bge-reranker-base
- VL-CLIP fine-grained attributes (arXiv 2507.17080): https://arxiv.org/pdf/2507.17080
- ESCI retrieval benchmark numbers (r2decide): https://r2decide.com/blog/evaluating-e-commerce-search-engines
