# 狮选 LionPick — 答辩幻灯片骨架 (Defense Deck Outline)

> A slide-by-slide scaffold for the ByteDance 2026 AI 全栈挑战赛 defense
> (2026-06-11 → 06-19). Each slide lists its **point**, **what to show**, and
> the **rubric dimension** it scores. Turn into real slides; keep ≤ 14 slides
> for a ~8-10 min talk + live demo. Pair with `docs/DEMO_RUNBOOK.md`.

Rubric (from `docs/RUBRIC_MAPPING.md`): 基础功能完整性 35% · 工程质量 25% ·
效果与可靠性 20% · 加分项 20%.

---

### 1 — 封面 / Title
- **狮选 LionPick** — 基于 RAG 的多模态电商智能导购 AI Agent (iOS).
- Team AAALion (陈澍枫 / 管图杰 / 李雨晟). One tagline: *"导购该懂货,而不是
  编货。"* (a guide that knows the catalog, never invents it.)

### 2 — 问题 / Why
- Generic chat assistants hallucinate prices/specs and give sycophantic
  "I couldn't find that". Shoppers need a guide that is **grounded in a real
  catalog**, multimodal (photo → product), and closes the loop to purchase.
- *Rubric: framing for 加分项 (differentiation).*

### 3 — 方案一图 / Architecture (60s)
- The diagram from `CLAUDE.md §2`: iPhone (SwiftUI) → FastAPI `/chat/stream`
  (SSE) → Chroma (text+image) + BM25 + cross-encoder rerank → vision LLM.
- Call out: **on-device app + cloud RAG backend, streaming**.
- *Rubric: 工程质量.*

### 4 — RAG 检索管线 / Retrieval
- Hybrid: dense (BGE) + BM25, fused (RRF), then **cross-encoder rerank**;
  structured constraint filters (category / brand / CNY budget / negation).
- *Rubric: 效果与可靠性.*

### 5 — 差异化①: 反幻觉来源标签 / Anti-hallucination ⭐
- Every factual claim carries **`[目录✓]`** (from catalog) or **`[推断?]`**
  (inferred), rendered green/amber in-app. The model is instructed + shown to
  never invent. *This is the headline differentiator — spend time here.*
- Show: a reply with the colored badges.
- *Rubric: 加分项 + 效果与可靠性.*

### 6 — 差异化②: 多模态 / Multimodal
- CLIP image collection: photo → same product. Voice in + TTS out.
- Show: photograph a product → matched card.
- *Rubric: 基础功能完整性 + 加分项.*

### 7 — 闭环能力 / Full loop
- Cart, **one-tap agentic order** (帮我下单), group-buy (拼单), price-watch
  (降价提醒), repurchase reminders (复购), per-account preferences (👍/👎).
- *Rubric: 基础功能完整性.*

### 8 — 多轮理解 / Conversation
- Persistent negation ("不要苹果" carries forward), budget/brand state,
  follow-ups ("再便宜点的") inherit context, topic-switch detection.
- *Rubric: 效果与可靠性.*

### 9 — 中英双语 / i18n (NEW)
- In-app language toggle (中文/English): whole UI + **the assistant's reply
  language** switch at runtime, no relaunch.
- Show: flip to English, ask "running shoes under 1000".
- *Rubric: 加分项.*

### 10 — 效果数据 / Eval
- recall@5 ≈ **0.88**, MRR ≈ **0.83**, **negation accuracy 1.000** on the
  audited 59-case set (92-case golden total). Per-scenario eval dashboard.
- *Rubric: 效果与可靠性 — lead with the numbers.*

### 11 — 工程质量 / Engineering
- Hybrid retrieval cache (cold ~15-18s → **warm ~1s**, measured), two cache
  layers; CI (iOS build + RAG eval + unsigned .ipa); **git-clone auto-deploy**
  to a GCP VM (~2 min, rollback on failure); 43 server tests + 92-case eval.
- *Rubric: 工程质量.*

### 12 — 现场演示 / Live demo
- Run the `DEMO_RUNBOOK.md §2` sequence. Backup video ready (PDF-recommended).

### 13 — 已知边界 + 路线 / Honesty + roadmap
- Free-tier Apple signing (7-day), single CPU-only demo VM (no GPU), WeChat /
  Apple sign-in are demo-stubbed (need paid/enterprise accounts). Next: real
  Doubao key, cloud cross-device sync, named-tunnel stable URL.
- *Showing limits honestly scores 工程质量 + credibility in Q&A.*

### 14 — 收束 / Close
- One sentence: a grounded, multimodal, closed-loop 导购 agent — *knows the
  catalog, never invents it.* Thanks + Q&A.

---

## Q&A prep (anticipate)
- **"如何防幻觉?"** → catalog-grounded prompt + source tags + "never invent"
  discipline; retrieval returns only real SKUs.
- **"延迟怎么办?"** → two cache layers + pre-warm; cold→warm ~16×.
- **"检索为什么用 hybrid?"** → dense catches meaning, BM25 catches exact
  tokens/SKUs, rerank fixes ordering; ablation in `docs/EVAL_RESULTS.md`.
- **"鉴权/会话安全?"** → (be ready) current demo uses a per-account token;
  production would issue a signed JWT.
- **"为什么 iOS?"** → multimodal capture (camera/voice) + the target user.
- **"规模化?"** → stateless API + caches + auto-deploy; swap CPU VM for GPU.
