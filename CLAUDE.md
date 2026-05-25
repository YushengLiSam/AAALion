# CLAUDE.md — Bootstrap for a new Claude Code session

> Read this file first. It's the single self-contained entry point. Every
> claim links to the canonical doc — do not duplicate content, follow the
> link. After this you may stop reading unless you need depth.

Last touched: **Round 7 (2026-05-25)**. Author: Shufeng Chen (陈澍枫).

---

## 1. What is 狮选 LionPick

**狮选 LionPick** is an iOS-first, RAG-based, multimodal Chinese e-commerce
导购 (shopping-assistant) AI agent — built for **ByteDance 2026 AI 全栈挑战赛**
(AI Full-Stack Challenge — *not* 工程训练营).

- **Code-freeze**: 2026-06-10.
- **Defense window**: 2026-06-11 → 2026-06-19.
- **Team**: AAALion (3 people). See `docs/POLICY.md` §Ownership.
- **Topic**: 基于 RAG 的多模态电商智能导购 AI Agent.
- **PDF rubric mapping**: see [`docs/RUBRIC_MAPPING.md`](docs/RUBRIC_MAPPING.md).

---

## 2. Architecture in 60 seconds

```
┌────────────┐          ┌──────────────┐         ┌──────────────────┐
│ iPhone 13  │ SSE/JSON │ FastAPI       │  query  │ Chroma (text +  │
│ 狮选 (SwiftUI)──────►  /chat/stream   ├────────►│ image collections)│
│            │          │ (server/app/) │  ──────►│ + BM25 corpus    │
└────────────┘          └──────┬───────┘   rerank │ + cross-encoder  │
                               │                  └──────────────────┘
                       (vision LLM call)
                               ▼
                  ┌──────────────────────┐
                  │ TokenRouter           │
                  │  claude-haiku-4-5     │
                  │  (multimodal, OpenAI- │
                  │  compatible API)      │
                  └──────────────────────┘
```

- Detailed diagram + design rationale: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- API surface: [`docs/API.md`](docs/API.md).
- Pipeline / dev SOP: [`docs/PIPELINE.md`](docs/PIPELINE.md).

---

## 3. What runs where

| Thing | Where | How to reach |
|---|---|---|
| Repo | `~/Desktop/rag/AAALion-/` on Shufeng's Mac | local |
| Backend | `uvicorn` on Mac, port `8000`, bound `0.0.0.0` | `aaalion backend` |
| Mac LAN IP | run `ipconfig getifaddr en0` each session | hardcoded in `client/AAALionApp/AAALionApp/Config.swift` (`defaultBackendURL`); also overridable from the in-app Settings sheet at runtime |
| iOS app | iPhone 13 Pro UDID `7310469E-E396-5197-9408-FF1AD58D4CF2` | `aaalion ios-device` |
| Chroma vector DB | in-process, persisted to `data/.chroma/` (gitignored) | implicit |
| A100 GPU | `ssh uc` (host alias in `~/.ssh/config`) | scope is `~/shufeng/AAALion-/` only |
| **Off-limits** | `~/shufeng/cuda-fuzzing/` on uc | **NEVER touch this dir** — it's another project |
| Credentials | `~/.config/lionpick/credentials.env` (mode 0700, OUTSIDE repo) | Apple Team ID, TokenRouter key, Mac password, Apple ID |
| Remote | `https://github.com/YushengLiSam/AAALion-.git` | branches `main` + `shufeng` |
| Hardware roster | [`docs/HARDWARE.md`](docs/HARDWARE.md) | — |

**Where Shufeng's local-only notes live**: `docs/POLICY_LOCAL.md` (gitignored).

---

## 4. Subsystem map

| Subsystem | Owner | Entry file | See-also |
|---|---|---|---|
| iOS app | Shufeng | `client/AAALionApp/AAALionApp/App.swift` | [`docs/IOS_SETUP.md`](docs/IOS_SETUP.md), [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md) |
| Backend | (Sam, currently solo Shufeng) | `server/app/main.py` | `server/README.md` |
| Chat route | Shufeng (impl) | `server/app/routes/chat.py` | event taxonomy in [`docs/API.md`](docs/API.md) |
| LLM provider | Shufeng | `server/app/services/llm_provider.py` | multi-provider switch via `.env` |
| Cache | Shufeng | `server/app/services/cache.py` | wired into chat route |
| RAG ingest | (Tujie, currently solo Shufeng) | `rag/ingest/chunk.py`, `embed_text.py`, `embed_image.py` | `rag/README.md` |
| RAG retrieve | Shufeng | `rag/retrieve/query.py` (orchestrator) | hybrid `hybrid.py`, BM25 `bm25.py`, rewrite `rewrite.py`, negation `negation.py`, rerank `rerank.py` |
| Eval | Shufeng | `rag/eval/run.py` + `rag/eval/golden.jsonl` (31 cases, 2 multi-turn) | report: recall@5=0.816, MRR=0.705 |
| iOS theme | Shufeng | `client/.../Views/Theme.swift` + `design-tokens.json` | from Claude design consult |
| Build automation | Shufeng | `Makefile` + `tools/aaalion` (global helper) | run `aaalion help` |

---

## 5. Current quality (Round 7, 2026-05-25)

| Dimension | Weight | Score | Note |
|---|---|---|---|
| 基础功能完整性 | 35% | 94 | unchanged |
| 工程质量 | 25% | 90 | +1 vs R6 (Sam's eval dashboard merged) |
| 效果与可靠性 | 20% | 82 | +2 vs R6.5 (brand-origin negation fix) |
| 加分项 | 20% | 84 | unchanged from R6 |
| **Total** | — | **~90 / 100** | up from R6 88.0 |

Round 7 highlights (see [`docs/QUALITY_REPORT_2026-05-25.md`](docs/QUALITY_REPORT_2026-05-25.md)
+ [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)):
- Sam's per-scenario eval dashboard merged (`601abb6`).
- Tujie's synonym + contextual-multi-turn + price-intent landed in R6.5
  (`b317081`, `4c2fe51`) — drove recall@5 0.684 → 0.816 on 31-case.
- Shufeng's brand-origin negation fix (`dc13f32`) closes the "不要日系" →
  安热沙 leak. Negation accuracy holds at 0.733 across 11 negation cases.
- Re-recorded demos: [`docs/demos/2026-05-25/`](docs/demos/2026-05-25/).

---

## 6. Round-by-round timeline

| Round | What landed | Commit record |
|---|---|---|
| 1 | Repo scaffold, README, screenshot watcher, A100 namespace | [`docs/commits/20260522-001-*`](docs/commits/) through `-006` |
| 2 | Multimodal SSE end-to-end; 6 demos; iPhone deploy; LAN networking; TROUBLESHOOTING.md | [`docs/commits/20260522-007-*`](docs/commits/) through `-009` |
| 3 | UX polish: theme + icon, settings sheet, edit-message, camera + files, voice + TTS, A100 CLIP retrieval | [`docs/commits/20260523-010-round4.md`](docs/commits/20260523-010-round4.md) (record for R3+R4 collated) |
| 4 | Files-importer bug fix, README icon top-right, `IMPLEMENTATION_GUIDE.md`, team proposal | same as Round 3 |
| 5 | Hybrid+rerank retrieval, structured negation filter, full 4.1 cart+checkout, eval measured, grader self-assessment | [`docs/commits/20260524-011-round5-rag-cart-grader.md`](docs/commits/20260524-011-round5-rag-cart-grader.md) |
| 6 | Real-product expansion (CN + Amazon), provenance UI, funny loading, inline cart, store deep links, this CLAUDE.md | [`docs/commits/20260524-013-round6-*`](docs/commits/) |
| 6.5 | **Tujie**: synonyms + contextual multi-turn + price intent. recall@5 0.684 → 0.816 on 31-case. | merge commits |
| 7 | **Sam**: 56-case per-scenario eval dashboard merged. **Shufeng**: brand-origin negation fix + re-recorded demos under `docs/demos/2026-05-25/`. | [`docs/commits/20260525-014-round7-*`](docs/commits/) |

Full archived plan from rounds 1-6: [`docs/PLAN_ARCHIVE.md`](docs/PLAN_ARCHIVE.md).

---

## 7. What's open (as of Round 6)

1. **Defense slide deck** — not started yet; 2026-06-11 is close.
2. **Demo video** (3-5 min) — PDF strongly encourages a backup video.
3. **Real Doubao key** — the PDF-provided key was deactivated after a leak;
   currently using TokenRouter as the primary LLM. Switch back if/when team
   gets a fresh Doubao key.
4. **Cert refresh** — Personal Team free-tier signing expires every 7 days
   (see [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) §Cert expiry).
   Run `aaalion resign` weekly.
5. **Sam / Tujie work** — see [`docs/PROPOSAL_2026-05-24.md`](docs/PROPOSAL_2026-05-24.md);
   default cadence is Shufeng plans solo until teammates respond.

---

## 8. Build & run (5 commands)

```bash
# 1. Backend (one shell).
aaalion backend

# 2. Re-ingest RAG if data/ changed (one-off, not every run).
aaalion ingest

# 3. iOS simulator.
aaalion ios-sim

# 4. iOS on device (after `aaalion resign` if cert is stale).
aaalion ios-device

# 5. RAG eval.
aaalion eval
```

`aaalion` is a global helper symlinked into `~/.local/bin/`; it walks up to
find the repo so it works from anywhere. Source: `tools/aaalion`.

Full teammate-onboarding guide: [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md).

---

## 9. Top 5 gotchas (and where the fix is documented)

1. **iPhone "cannot connect to server"** — backend was bound to `127.0.0.1`.
   Fix: bind `0.0.0.0`; also hardcode LAN IP in `Config.swift`. See
   [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) §LAN networking.
2. **`make ios` failed from $HOME** — Makefile didn't walk up to repo.
   Fix: use `aaalion ios-sim`/`-device` instead (the helper handles it).
3. **`.fileImporter` returns error** — iCloud-Drive files not yet downloaded
   need `NSFileCoordinator`. Fix shipped in Round 4. See `ChatView.swift:91`.
4. **Cert ID ≠ Team ID** — Xcode shows certificate ID (`7TQ694CBJV`) after
   the email; real team ID (`V8KDBHKA3P`) is in the `.mobileprovision`
   under `TeamIdentifier`. Use `security cms -D -i <profile>` to extract.
   Stored in `~/.config/lionpick/credentials.env`.
5. **SSE parser hang on iOS 17/18** — `URLSession.bytes.lines` elides the
   blank-line event separator. Fix: parse each `data:` line directly. See
   `client/.../Services/ChatService.swift:84-100`.

Every other recurring hiccup: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

---

## 10. Conventions

- **Commits**: Conventional Commits (`type(scope): summary`). See
  [`docs/POLICY.md`](docs/POLICY.md) §Commit message format. Examples:
  `feat(client): inline cart add`, `fix(server): retry on 429`,
  `docs(repo): round 6 progress`.
- **Branch model**: `shufeng` first → FF-merge to `main` after the grader
  self-assessment is written and the diff is reviewed. See
  [`docs/POLICY.md`](docs/POLICY.md) §Branch model.
- **Policy mechanism**: when Shufeng says "store X in policy", it lands in
  [`docs/POLICY.md`](docs/POLICY.md) (shared) or `docs/POLICY_LOCAL.md`
  (gitignored, private).
- **Major commits get a record file**: every "feel-the-weight" commit lands
  with a sibling `docs/commits/YYYYMMDD-NNN-<topic>.md`. See
  [`docs/commits/README.md`](docs/commits/README.md).
- **Author**: every commit is attributed to
  `Shufeng Chen <shufeng.c.dev@gmail.com>` (verified in `git log`).
- **Secrets**: never committed. Pre-commit hook lives at
  `tools/git-pre-commit.sh`; runs `tools/check-secrets.sh`.
- **A100 boundary**: every shell on uc must be inside `~/shufeng/AAALion-/`.
  Never `cd ../` past it. Never touch `~/shufeng/cuda-fuzzing/`.

---

## 11. Starting fresh in a new Claude Code session

Paste this prompt as your first message:

> I'm working on 狮选 LionPick (the iOS-first RAG shopping-assistant AI
> agent for ByteDance 2026 AI 全栈挑战赛). The repo is at
> `~/Desktop/rag/AAALion-/`. Read `CLAUDE.md` first, then ask before doing
> anything destructive. Branch policy: commits land on `shufeng`, FF-merge
> to `main` only after grader self-review. Hard rules: never touch
> `~/shufeng/cuda-fuzzing/` on uc, never commit secrets, all commits
> attributed to `Shufeng Chen <shufeng.c.dev@gmail.com>`. Credentials live
> at `~/.config/lionpick/credentials.env` (mode 0700, outside repo).

Then describe what you want to change. The agent has everything it needs
to load the rest from the docs linked above.

---

## 12. Last-resort checklist before shipping

- [ ] `aaalion eval` passes (recall@5 ≥ 0.70, MRR ≥ 0.65)
- [ ] iPhone 13 Pro launches, shows the lion icon, streams a reply
- [ ] 6+ demo scenarios in `docs/demos/<latest>/` all PASS
- [ ] `tools/check-secrets.sh` is clean
- [ ] `git log --pretty='%an <%ae>'` shows only the canonical author
- [ ] `ssh uc 'stat -c %y ~/shufeng/cuda-fuzzing'` mtime unchanged
- [ ] `docs/QUALITY_REVIEW.md` is current
- [ ] `main` is at the same SHA as `shufeng` (FF merge done)
- [ ] `docs/commits/<latest>.md` records what shipped

If any item is red, document why in the commit record so the next reader
isn't confused.
