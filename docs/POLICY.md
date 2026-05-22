# Project Policy

Durable team rules and preferences. Anything in this file is **shared** with all teammates (committed and pushed). Private entries go in `docs/POLICY_LOCAL.md` (gitignored).

When Shufeng says "store X in policy" in conversation with the project assistant, the entry lands here (or `POLICY_LOCAL.md` if marked private).

## Scope and identity

- **Competition**: ByteDance 2026 AI 全栈挑战赛. This is the AI Full-Stack Challenge — **not** the 工程训练营.
- **Topic**: 基于 RAG 的多模态电商智能导购 AI Agent.
- **Deadline**: 2026-06-10 (code-freeze). Defense window: 2026-06-11 to 2026-06-19.

## Ownership

- **Shufeng Chen** — iOS client (`client/`).
- **Yusheng Li (Sam)** — Backend (`server/`).
- **Tujie Guan** — RAG / retrieval (`rag/`).
- Cross-area changes require the affected owner's approval before merge.

## Secrets

- The Doubao API key is shared via the team's private channel only, dropped into `.env` locally. It is **never** committed, never pushed, never sent to the iOS client.
- `.env` is in `.gitignore`; the example template lives at `.env.example`.

## Branch model

- `main` is the stable branch.
- Each developer has a personal branch: `shufeng`, `sam`, `tujie`.
- Most of Shufeng's ongoing development happens on `shufeng` to protect `main` stability.
- PRs are squash-merged into `main`.

## A100 (SSH UC) — boundaries

- All project work lives under `~/shufeng/AAALion-/`.
- The existing `~/shufeng/cuda-fuzzing/` is a different ongoing task and is strictly off-limits.
- No changes outside `~/shufeng/` (no system packages, no shell rc edits).

## Hallucination rules

- The agent never invents prices, SKUs, coupons, or product names.
- Product cards rendered on the client come from indexed JSON only.
- If retrieval misses, the system prompt instructs the model to admit it.

## Bonus feature commitments

- We commit to **two** bonus tracks (per the PDF "做精一项胜过浅尝三项" guidance):
  1. **4.3 conversational depth** — multi-turn, negation, comparison.
  2. **4.2 multimodal — 拍照找货** — image-to-product via CLIP on A100.
- Voice / TTS / cart / ordering are **out of scope v1**.

## Data

- The bundled `data/seed/` is AI-generated (confirmed by recruiters). It serves as a smoke-test set.
- The demo and eval must use **real** product data. See `docs/DATA.md` for sourcing.

## Documentation discipline

- Every doc in `docs/` is written assuming the reader is a cold-start teammate.
- Meeting notes live in `meetings/` with the file name format `YYYY-MM-DD-topic.md`.

## Decision log

Use this section to record decisions that change the architecture or scope.

| Date | Decision | Rationale |
|---|---|---|
| 2026-05-22 | Switch client from Android (Sam's WeChat proposal) to **iOS**. | Shufeng owns the client and prefers iOS; iPhone 13 available for testing. |
| 2026-05-22 | **Qdrant** as primary vector DB, Chroma as fallback. | Better multi-vector + filter support; still single-container Docker for private deployment. |
| 2026-05-22 | A100 used for **index-build only**, not request-path serving. | Keeps the backend portable; A100 is overkill for serving. |
