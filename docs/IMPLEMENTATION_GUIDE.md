# Implementation Guide — 狮选 LionPick

> A single-page index for anyone new to the repo (teammates, judges, future-Shufeng). This page **does not duplicate** other docs — it links to them by topic and by intent.

## What is 狮选 LionPick

A native iOS shopping-assistant app for ByteDance's 2026 AI 全栈挑战赛. The user describes (or photographs) a product need; the app retrieves real products from a vector index, calls a vision-capable LLM grounded on the retrieved catalog, and streams the recommendation back as text + tappable product cards.

## Architecture in 60 seconds

```
 ┌────────────┐  text+image   ┌──────────┐   text   ┌──────────────┐
 │  iOS app   │ ─────────────►│ FastAPI  │ ───────► │  Chroma text │
 │ (SwiftUI)  │                │  /chat   │          │  1082 chunks │
 │ Speech /   │ ◄────SSE──────│  /stream │ ◄───────  └──────────────┘
 │ AVSpeech / │                └──────────┘   image   ┌──────────────┐
 │ Photos /   │                     │ ▲     ────────► │ Chroma image │
 │ Camera /   │                prompt│ │ deltas       │   100 vectors│
 │ Files      │                     ▼ │     ◄───────  └──────────────┘
 └────────────┘             ┌─────────────────┐                ▲
                            │  TokenRouter:   │           CLIP │ via OpenCLIP
                            │ claude-haiku-4-5│           on A100
                            └─────────────────┘
```

Depth in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Subsystem map

| Subsystem | Owner | Key files | Deep-dive doc |
|---|---|---|---|
| iOS chat UI | 陈澍枫 | `client/AAALionApp/AAALionApp/Views/ChatView.swift` + `MessageBubbleView.swift` | [`IOS_SETUP.md`](IOS_SETUP.md) |
| iOS view model + state | 陈澍枫 | `client/.../ViewModels/ChatViewModel.swift` (@Observable) | — |
| iOS networking (SSE) | 陈澍枫 | `client/.../Services/ChatService.swift` | [`API.md`](API.md) |
| iOS image input (3 sources) | 陈澍枫 | `Views/CameraPicker.swift` + PhotosPicker + .fileImporter | — |
| iOS voice (in + out) | 陈澍枫 | `Services/SpeechService.swift` (zh-CN ASR) + `Services/TTSService.swift` | — |
| iOS settings | 陈澍枫 | `Views/SettingsView.swift` (UserDefaults-persisted backend URL) | — |
| iOS theme | 陈澍枫 | `Views/Theme.swift` + `client/AAALionApp/design-tokens.json` | — |
| Backend SSE route | 李雨晟 | `server/app/routes/chat.py` (text + multimodal) | [`API.md`](API.md) |
| LLM provider abstraction | 李雨晟 | `server/app/services/llm_provider.py` (TokenRouter / Anthropic / Doubao / OpenAI / Echo) | [`POLICY.md`](POLICY.md) §"Secrets" |
| Backend caching | (proposed) | `server/app/services/cache.py` (written; wiring deferred) | [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) |
| Text RAG | 管图杰 | `rag/retrieve/constraints.py` + `query.py` + `hybrid.py` (bge-small-zh-v1.5, BM25, constraint filtering) | [`ARCHITECTURE.md`](ARCHITECTURE.md) §"3. RAG" |
| Image RAG (CLIP) | 管图杰 | `rag/ingest/embed_image.py` (OpenCLIP ViT-B/32) + `rag/ingest/run_image.py` | [`HARDWARE.md`](HARDWARE.md) §"A100" |
| Prompt | 管图杰 | `rag/prompts/system.md` | — |
| Eval | 管图杰 | `rag/eval/golden.jsonl` + `rag/eval/report.py` (64-case dashboard) | [`EVAL_RESULTS.md`](EVAL_RESULTS.md) |
| Seed data (100 products) | 管图杰 | `data/seed/{1..4}_<category>/data/*.json` + `images/*.jpg` | [`DATA.md`](DATA.md) + [`research/`](research/) |
| Toolchain | 陈澍枫 | `Makefile` + `tools/aaalion` + `tools/check-secrets.sh` | — |
| A100 SSH workflow | 陈澍枫 | `tools/ssh_a100.sh` | [`HARDWARE.md`](HARDWARE.md) |

## Build & run flow

```bash
# 5 commands from fresh clone to running app on simulator
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example server/.env   # set TOKENROUTER_API_KEY
aaalion ingest && aaalion backend &
aaalion ios-sim               # builds + installs + launches
```

For physical iPhone deploy and the weekly resign cadence: [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md).

For the A100 CLIP image index build (already done; one-time per dataset change):
```bash
ssh uc
cd ~/shufeng/AAALion-
source .venv/bin/activate
CHROMA_TELEMETRY=False python -m rag.ingest.run_image
# (back on Mac:) rsync -az uc:~/shufeng/AAALion-/data/.chroma/ data/.chroma/
```

## Implementation timeline (3 rounds in 3 paragraphs)

**Round 1 (2026-05-22 night, [`commits/20260522-001..003`](commits/))**: empty workspace → full repo scaffold. iOS skeleton (SwiftUI, MVVM, hand-rolled SSE), FastAPI scaffold with fixture stream, RAG module with chunker + retrieval stubs, 100-product seed dataset extracted, 9 docs written, tools/screenshot_watcher.py, git remote configured, A100 namespace at `~/shufeng/AAALion-/` (separate from `cuda-fuzzing/`).

**Round 2 (2026-05-22 evening through 2026-05-23 dawn, [`commits/20260522-004..009`](commits/))**: real ingest into Chroma with `bge-small-zh-v1.5` embeddings (992 chunks). Multi-provider LLM (TokenRouter / Anthropic / Doubao / OpenAI / Echo). Multimodal payload (Pydantic v2 content union). Verified end-to-end on iPhone 17 Pro simulator. iPhone 13 Pro device deploy (Personal Team signed via `V8KDBHKA3P`). LAN networking bug surfaced and fixed (`Config.swift` LAN URL + uvicorn 0.0.0.0). Six demos recorded with verdicts. Three Perplexity research outputs ingested into `docs/research/`. Honest assessment: no usable real Chinese e-commerce dataset publicly available.

**Round 3 (2026-05-23 morning, [`commits/20260522-010`](commits/) + later)**: UX polish + A100 actually used. Settings screen with `UserDefaults`-persisted backend URL. Edit/Copy/Speak context menu. Camera + Files attachment alongside Photos. Voice input via `Speech.framework`. TTS via `AVSpeechSynthesizer`. Claude-designed warm-ivory + amber-gold theme. App icon generated via TokenRouter `openai/gpt-5.4-image-2`. **A100 CUDA working with cu124 torch (no system driver touched)**; 100 product images embedded with OpenCLIP in <10 seconds; image-first retrieval wired in backend. Three new demo screenshots in `docs/demos/2026-05-23/`. `RUBRIC_MAPPING.md` documents every PDF §4 sub-item.

## Where to look next (task-oriented)

| If you want to... | Start here |
|---|---|
| Change the LLM model | `server/app/services/llm_provider.py` + `.env.example` ([`POLICY.md`](POLICY.md) §"Secrets") |
| Add a new product category | [`DATA.md`](DATA.md) §"Schema" + [`research/`](research/) for what's available |
| Wire the hot-query cache | `server/app/services/cache.py` (written) + [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) item #3 |
| Debug an iPhone issue | [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — Untrusted Developer, LAN networking, file picker, cert expiry, etc. |
| Reproduce the iPhone deploy | [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) (45 min from fresh clone) |
| Prepare for defense (6/11) | [`RUBRIC_MAPPING.md`](RUBRIC_MAPPING.md) + [`demos/`](demos/) + [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) |
| Understand a decision | [`HONEST_ANSWERS.md`](HONEST_ANSWERS.md) + [`commits/`](commits/) record files |
| Plan the next iteration | [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) — currently awaiting team review |
| Onboard a new teammate | this file → [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) → [`PIPELINE.md`](PIPELINE.md) → area-specific README in `client/server/rag/` |

## Conventions

- **Commits**: Conventional Commits (`<type>(<scope>): <summary>`). Rule in [`POLICY.md`](POLICY.md).
- **Major commits get a record file** under `docs/commits/<YYYYMMDD>-<NNN>-<slug>.md`. Rule in `docs/POLICY_LOCAL.md` (gitignored; the records themselves are committed).
- **Secrets stay outside the repo**: `~/.config/lionpick/credentials.env` + `server/.env` (both gitignored). Pre-commit `tools/check-secrets.sh` scans for ARK/Anthropic/OpenAI key shapes.
- **A100 boundaries**: every command runs under `~/shufeng/AAALion-/`. Never `cd` into `~/shufeng/cuda-fuzzing/` (different active project). [`HARDWARE.md`](HARDWARE.md) has the hard rules.

## Defense ready-state checklist

- [x] End-to-end loop works (demos in `docs/demos/2026-05-23/`)
- [x] iPhone deploy verified
- [x] Anti-hallucination evidence (`02-conditional-filter.md`)
- [x] Multimodal (vision LLM + CLIP both wired)
- [x] Voice in + voice out
- [x] Multi-turn + negation + comparison
- [x] Rubric mapping documented
- [ ] Real product data (5-10 hand-curated; [`research/`](research/) explains why none are off-the-shelf)
- [ ] Cache wired (`services/cache.py` ready)
- [ ] Demo video recorded (3-5 min)
- [ ] Defense slide deck
- [ ] Weekly cert re-sign cadence respected (current cert expires ~2026-05-29)

Outstanding items are the substance of [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) — team please weigh in.
