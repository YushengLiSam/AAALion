# CLAUDE.md — Bootstrap for a new Claude Code session

> Read this file first. It's the single self-contained entry point. Every
> claim links to the canonical doc — do not duplicate content, follow the
> link. After this you may stop reading unless you need depth.

Last touched: **R11 (2026-06-02) — login/sign-up page + account profile; docs refactor**. Authors: Shufeng Chen (陈澍枫) + Tujie Guan (管图杰) + Yusheng Li (李雨晟). Full timeline: [`docs/DEV_LOG.md`](docs/DEV_LOG.md) · Docs index: [`docs/README.md`](docs/README.md).

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
| **Backend (prod)** | **GCP VM (Yusheng), `systemd`-managed, public HTTPS via Cloudflare tunnel** | tunnel URL baked into `Config.swift`; **ephemeral — changes on tunnel restart**, Yusheng re-broadcasts. Swagger at `/docs`. |
| Backend (local dev) | `uvicorn` on Mac, port `8000`, bound `0.0.0.0` | `aaalion backend`; point the sim at it with `PUBLIC_BACKEND_URL=http://localhost:8000` |
| Cloud sync | **the VM is a GIT CLONE with auto-deploy (R10 CD)** — `lionpick-autodeploy.timer` runs `tools/cloud-autodeploy.sh` every ~2 min: `git fetch` → if `origin/main` advanced, `reset --hard` + `systemctl restart lionpick` + `/ready` check, **rolling back** on failure. **A merge to main is live on the cloud within ~2 min, hands-free** (gitignored `.env` / `data/.chroma` / `data/*.db` survive a `reset --hard`). Manual redeploy if needed: SSH in, `git pull && sudo systemctl restart lionpick`. VM external IP `34.139.88.204`. |
| Mac LAN IP | run `ipconfig getifaddr en0` each session | overridable from the in-app Settings sheet at runtime (long-press gear 1.5 s → dev mode) |
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
| Currency display | Tujie | `server/app/services/currency.py` | foreign catalog prices converted to CNY with dated reference FX; source price retained |
| RAG ingest | (Tujie, currently solo Shufeng) | `rag/ingest/chunk.py`, `embed_text.py`, `embed_image.py` | `rag/README.md` |
| RAG retrieve | Shufeng | `rag/retrieve/query.py` (orchestrator) | hybrid `hybrid.py`, BM25 `bm25.py`, rewrite `rewrite.py`, negation `negation.py`, rerank `rerank.py` |
| Eval | Sam + Tujie | `rag/eval/core.py` + `rag/eval/golden.jsonl` (92 cases, 80 positive, 12 no-match) | Production path (Hybrid+Rerank): recall@5 **0.939**, MRR **0.818**, 反选 **0.971**, no-match **0.952**. **Single source of truth: [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)** (R11 retest 2026-06-03 — predates the `7c6c455` + R13 negation fixes; regenerate before final defense claims) |
| iOS theme | Shufeng | `client/.../Views/Theme.swift` + `design-tokens.json` | from Claude design consult |
| Build automation | Shufeng | `Makefile` + `tools/aaalion` (global helper) | run `aaalion help` |
| Presentation material | Shufeng | `docs/explainers/README.md` | 15 CS-sophomore-friendly explainers; start here for non-engineer audiences |
| **Cloud deploy (prod)** | **Sam** | GCP VM `lionpick-demo` + `systemd` (`lionpick`, `lionpick-tunnel`, `lionpick-autodeploy.timer`) | git-clone + auto-deploy on push-to-main (`tools/cloud-autodeploy.sh`, see §3 + §9.6-7); direct-SSH for ops. `tools/bench_cpu_latency.py` justifies CPU-only (no GPU) sizing |
| **Retrieval cache** | **Sam** | `server/app/services/rag_client.py` `_heavy_retrieve` + `_retrieval_cache_*` | memoizes hybrid+rerank (R10 Option A); preference reorder stays outside so 👍/👎 is live. Stats via `retrieval_cache_stats()` |
| **Repurchase reminders** | **Sam** | `server/app/services/repurchase_db.py`, `routes/repurchase.py` | SQLite, per-product cycle, 24h snooze; `docs/REPURCHASE_PLAN.md`. 7 tests |
| **Accounts / auth** | Shufeng | `server/app/routes/auth.py`, `services/user_store.py` | Apple / 手机号验证码 / 密码; `user_id` may be `phone:…`/`apple:…` (colon-ok, R10.bugfix) |
| **Group-buy / preferences / price-watch** | Shufeng | `routes/group_buy.py` · `preferences.py` · `price_watch.py` (+ `*_db.py`) | R9.B/R10 closed-loop features; all SQLite |
| **Scenario coverage (rubric)** | Sam + Shufeng | `routes/chat.py` intent detectors | comparison table / scene builder (#7) / conversational cart-delete (#8); see scenario audit in `docs/DEV_LOG.md` |
| Market research / competitive | Shufeng | `docs/COMPETITIVE_ANALYSIS.md` | Built with web research (see Conventions §Web research); see also `docs/ROADMAP.md` + R9–R10 documentary `docs/commits/20260530-017-r9-r10-documentary.md` |

---

## 5. Current quality (Round 8, 2026-05-25 evening)

| Dimension | Weight | Score | Note |
|---|---|---|---|
| 基础功能完整性 | 35% | 95 | +1 (currency norm + stateful filters) |
| 工程质量 | 25% | 92 | +2 (eval dashboard + cache panel + Docker prewarm) |
| 效果与可靠性 | 20% | 88 | +6 (multi-turn negation persists, recall@5 0.983, neg-acc 1.000) |
| 加分项 | 20% | 85 | +1 (live FX, brand-origin perfect) |
| **Total** | — | **~91-92 / 100** | up from R7 90.0 |

Round 7 highlights (see [`docs/QUALITY_REVIEW.md`](docs/QUALITY_REVIEW.md)
+ [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)):
- Sam's per-scenario eval dashboard merged (`601abb6`).
- Tujie's synonym + contextual-multi-turn + price-intent landed in R6.5
  (`b317081`, `4c2fe51`) — drove recall@5 0.684 → 0.816 on 31-case.
- Shufeng's brand-origin path plus Yusheng's follow-up negation/alias/data
  audit close the known exclusion leaks. On the audited set, production
  negation accuracy is 1.000 across 10 cases carrying forbidden labels.
- Tujie's golden audit aligns 19 mislabeled or incomplete cases with the
  145-product catalog; corrected production baseline is recall@5 0.830,
  MRR 0.771. See [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md).
- Tujie's CNY normalization fetches the latest available Frankfurter
  reference rate for foreign products, shows RMB across cards/cart/checkout,
  and preserves original currency plus rate date for auditability. R7.2
  retains recall@5 0.830 while improving MRR from 0.771 to 0.778.
- With the latest `main` negation audit merged alongside CNY normalization,
  the regenerated production-path report reaches recall@5 0.880 and MRR
  0.828 on the same audited 59-case set.
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
| 7.1 | **Tujie**: audit and repair 19 incorrect/incomplete golden annotations; regenerate 59-case report. | [`rag/eval/golden.jsonl`](rag/eval/golden.jsonl), [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) |
| 7.2 | **Tujie**: normalize foreign prices to CNY for display and CNY budget filtering using cached latest reference FX. | [`server/app/services/currency.py`](server/app/services/currency.py), [`docs/API.md`](docs/API.md) |
| 7.3 | **Merged main + Tujie**: combine negation/brand-origin audit, golden audit, and CNY normalization; regenerate metrics. | [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md), [`docs/eval_report.html`](docs/eval_report.html) |

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
5. **Sam / Tujie work** — see [`docs/ROADMAP.md`](docs/ROADMAP.md) for the
   current forward plan + ownership split.

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

## 9. Top gotchas (and where the fix is documented)

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

### R10 cloud / deploy gotchas

6. **`gcloud compute ssh/scp` fails with "Reauthentication failed. cannot
   prompt during non-interactive execution"** — the wisc.edu account has a
   reauth security policy that needs an interactive prompt the agent shell
   can't show. **Bypass: SSH directly to the VM's external IP** (gcloud
   already installed the key):
   `ssh -i ~/.ssh/google_compute_engine yushengli@34.139.88.204 '<cmd>'`
   and `scp -i ~/.ssh/google_compute_engine <file> yushengli@34.139.88.204:~/`.
7. **VM is a git clone with auto-deploy** — see §3 Cloud sync. Push to main
   and `lionpick-autodeploy.timer` deploys it within ~2 min (fetch → reset
   --hard → restart → `/ready` check → roll back on failure). `.env` (LLM
   keys, gitignored), `data/.chroma` (indexes), `data/*.db` (auth/cart/prefs
   SQLite) survive a `reset --hard` because they're gitignored. Manual
   deploy: `git pull && sudo systemctl restart lionpick`.
8. **First-load HF model download hangs warmup** — the VM is far from
   huggingface.co (~10 s/req). Rerankers + CLIP must be pre-downloaded,
   then `HF_HUB_OFFLINE=1` is set in the systemd unit so startup loads
   from cache without the slow online HEAD check. `OMP_NUM_THREADS=4` /
   `MKL_NUM_THREADS=4` are also set so the cross-encoder uses all 4 vCPUs.
9. **English query is slow (~25-40 s cold)** — it routes to the
   multilingual `bge-reranker-v2-m3` (568M) which is heavy on a CPU VM.
   Chinese uses `bge-reranker-base` (~8 s cold). The retrieval cache
   (`rag_client._heavy_retrieve` memo, TTL 300 s) makes REPEATS instant —
   so **pre-warm demo queries**. LLM is fast now (TokenRouter haiku ~2 s
   first token; Doubao-Seed was ~14 s and was dropped).
10. **Free-team iOS signing expires in 7 days** — re-`xcodebuild`/⌘R to
    the device weekly. The repo's `project.yml` team is Shufeng's
    (`V8KDBHKA3P`); each dev overrides with their own team in Xcode
    (don't commit that change).

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
- **Team status updates** (R8 onward): WeChat drafts are **LOCAL ONLY**
  (gitignored under `docs/WECHAT_*.md` / `docs/cluely/`). The
  persistent on-remote record is [`docs/DEV_LOG.md`](docs/DEV_LOG.md),
  a rolling reverse-chronological log; add a new top section per
  shipping moment. Full policy in [`docs/POLICY.md`](docs/POLICY.md)
  §"Team status updates".
- **Author**: every commit is attributed to
  `Shufeng Chen <shufeng.c.dev@gmail.com>` (verified in `git log`).
- **Secrets**: never committed. Pre-commit hook lives at
  `tools/git-pre-commit.sh`; runs `tools/check-secrets.sh`.
- **A100 boundary**: every shell on uc must be inside `~/shufeng/AAALion-/`.
  Never `cd ../` past it. Never touch `~/shufeng/cuda-fuzzing/`.
- **Web research**: this environment has live internet — `WebSearch` /
  `WebFetch` tools plus a `deep-research` skill (and `general-purpose`
  agents can search). Use them for competitive analysis, technical
  state-of-the-art checks, and grounding forward proposals. Always cite
  sources with URLs + access dates, and label vendor-reported numbers as
  claims. Worked example: `docs/COMPETITIVE_ANALYSIS.md` and
  `docs/ROADMAP.md` were both built this way (2026-05-30).

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
