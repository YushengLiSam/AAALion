# Project Policy / 项目政策

Durable team rules and preferences. Anything in this file is **shared** with all teammates (committed and pushed). Private entries go in `docs/POLICY_LOCAL.md` (gitignored).

When Shufeng says "store X in policy" in conversation with the project assistant, the entry lands here (or `POLICY_LOCAL.md` if marked private).

## Scope and identity / 比赛与课题

- **Team / 团队名**: AAALion (3 人，编队名).
- **Product / 产品名**: 狮选 **LionPick** — 基于 RAG 的多模态电商智能导购 (Lion's Pick of the right product).
- **Competition / 比赛**: ByteDance 2026 AI 全栈挑战赛. This is the AI Full-Stack Challenge — **not** the 工程训练营.
- **Topic / 课题**: 基于 RAG 的多模态电商智能导购 AI Agent.
- **Deadline / 截止**: 2026-06-10 (code-freeze). Defense window: 2026-06-11 to 2026-06-19.

## Ownership / 分工

| 中文名 | 英文名 / 昵称 | 模块 | 主要分支 |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | iOS 客户端 `client/` (+ fallback for everything) | `shufeng` |
| 李雨晟 | Yusheng Li | 后端 `server/` | `sam` |
| 管图杰 | Tujie Guan | RAG 检索 `rag/` | `tujie` |

Cross-area changes require the affected owner's approval before merge.

> **Important / 重点**: Shufeng acts as the project lead and the **fallback owner for every area**. If a teammate's deliverable slips, Shufeng owns the gap. Plan accordingly — see [SOLO_DEV_PLAN.md](SOLO_DEV_PLAN.md).

## Secrets

- The Doubao API key is shared via the team's private channel only, dropped into `.env` locally. It is **never** committed, never pushed, never sent to the iOS client.
- `.env` is in `.gitignore`; the example template lives at `.env.example`.

### 2026-05-22: Doubao key leak incident (organizer announcement)

The original Doubao API key printed in the competition PDF was leaked by another team via an open-source GitHub commit. The leaked key was abused by non-participants, blocking legitimate use, and the organizer **deactivated it**. New keys will be re-distributed.

Operational rules until further notice:

- The PDF-provided key returns HTTP 401 — do not bother using it.
- **Never commit any API key**, ever, even momentarily. Use `.env` only. Pre-commit hooks (see [`tools/check-secrets.sh`](../tools/check-secrets.sh)) can help.
- If you spot a leak in any team member's repo or branch, **rotate immediately** and notify the channel.
- Until the new Doubao key arrives, use the Anthropic Claude provider (`LLM_PROVIDER=anthropic` in `.env`) — see [docs/API.md](API.md) and [server/README.md](../server/README.md).

## Branch model

- `main` is the stable branch.
- Each developer has a personal branch: `shufeng`, `sam`, `tujie`.
- Most of Shufeng's ongoing development happens on `shufeng` to protect `main` stability.
- PRs are squash-merged into `main`.

## Commit message format / 提交信息格式

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <one-sentence summary in present tense>

<optional body — why, not what>
```

**Types**:
- `feat` — new feature
- `fix` — bug fix
- `docs` — documentation only
- `chore` — tooling, infra, deps, repo hygiene
- `refactor` — code change with no functional difference
- `test` — adding/updating tests
- `style` — formatting only

**Scopes** (match the repo layout):
- `client` (iOS) · `server` (backend) · `rag` (retrieval)
- `docs` · `meetings` · `data` · `tools` · `repo` (repo-wide)

**Examples**:
```
feat(client): wire ChatService to real backend SSE
fix(server): handle Doubao 429 with exponential backoff
docs(policy): add commit message format
chore(tools): add Makefile and xcodegen project.yml
```

The summary line stays under 70 chars when possible. Bullets in the body, not in the subject.

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
| 2026-05-22 | Product name = **狮选 LionPick** (distinct from team name AAALion). | Bilingual, brandable, ties to lion identity, descriptive of "AI picks the right product." |
| 2026-05-22 | Adopt Conventional Commits as the commit format. | Easier to scan history; supports future tooling (changelog generation). |
| 2026-05-22 | Shufeng is the **fallback owner for every area** (solo-dev posture). | Risk mitigation if teammate output slips before 06-10. |
