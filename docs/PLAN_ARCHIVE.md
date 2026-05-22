# Plan: AAALion- Project Bootstrap & Pipeline

## Context

**Competition**: ByteDance 2026 AI 全栈挑战赛 (AI Full-Stack Challenge — NOT 工程训练营).
**Topic**: 基于 RAG 的多模态电商智能导购 AI Agent (RAG-based multimodal e-commerce shopping AI agent).
**Today**: 2026-05-22. **Code-freeze**: 2026-06-10. **Defense window**: 2026-06-11 to 2026-06-19. ~20 dev days left.

**Team** (1 month sprint, three people):
- **Shufeng Chen** (me, "小淫猫") — **iOS client** (Swift/SwiftUI). Sam's WeChat note had me on Android — I am switching to iOS.
- **Yusheng Li ("Sam")** — Python + FastAPI backend, streaming API, orchestration.
- **Tujie Guan** — RAG: embedding, vector store, retrieval, prompt engineering.

**Why this plan now**: We have a workspace (`~/Desktop/rag`) with reference PDFs, a toy dataset (zip), meeting notes, and a WeChat thread that captures the original (Android-era) division of labor. There is no scaffolded code yet. The user (Shufeng) has accepted an invite to a private remote at `https://github.com/YushengLiSam/AAALion-`. We need a firm, professional repo skeleton, a working screenshot-capture utility, a documented dev pipeline that all three teammates can follow, and a clear story for hardware (MacBook, iPhone 13, A100 over SSH, future Mac mini M4).

**Intended outcome after this plan executes**:
1. Local repo under `~/Desktop/rag/AAALion-/` populated with `client/`, `server/`, `rag/`, `docs/`, `meetings/`, `data/`, `tools/`, README, .gitignore, LICENSE.
2. `tools/screenshot_watcher.py` running manually saves clipboard screenshots from `shift+ctrl+command+4` into `screenshots/` with deterministic names.
3. `docs/POLICY.md` (shared, NOT gitignored) capturing durable preferences — the persistent file the user asked for.
4. `docs/HARDWARE.md` recording MacBook + iPhone 13 + (planned) Mac mini M4 + A100 SSH-UC roles.
5. `docs/PIPELINE.md` — dev/test/iterate guidance the team can read.
6. `docs/ARCHITECTURE.md` — end-to-end system design.
7. Initial commit on `main`, then a `shufeng` branch tracking `main`.
8. Remote pushed to `AAALion-` (if access works); otherwise everything is local-ready and the user runs the exact git commands they pasted.
9. A `shufeng/` subfolder on the A100 (created over SSH) reserved for indexing/embedding experiments — leaves `gpu-fuzz/` untouched.
10. A documentation pass aimed at Sam + Tujie: summary, detailed implementation, future-work notes.

---

## Tech Stack (Firm Choices)

Sam's WeChat proposal (Kotlin + Compose + Chroma + FastAPI) was the Android-era version. I am diverging on the client and proposing two backend upgrades for the team to ratify.

| Layer | Choice | Rationale |
|---|---|---|
| iOS Client | **Swift 5.9 + SwiftUI, target iOS 17.0** | SwiftUI's `Text` interpolation + `ScrollView` + `@Observable` give 80% of the chat UI in a day. iOS 17 is stable, well-documented, and iPhone 13 supports it. UIKit would burn 5+ days on collection-view boilerplate. |
| iOS SSE | **Hand-rolled on `URLSession.bytes(for:)`** | No third-party SSE pkg needed; ~80 lines wrap `AsyncSequence<UInt8>` into `AsyncStream<ChatDelta>`. Avoids dependency drift. |
| iOS State | **`@Observable` (iOS 17)** + MVVM | Skip TCA/Combine; one `ChatViewModel` per conversation. |
| Backend | **Python 3.11 + FastAPI + SSE** (as Sam proposed) | Streaming endpoints in a few lines. Pydantic for schemas. `uvicorn` for serving. |
| Vector DB | **Qdrant** (default) with Chroma as a fallback | Qdrant has first-class multi-vector / hybrid filter support — important for our category + price + brand filters that Chroma handles awkwardly. Qdrant runs as a single Docker container locally, so private deployment is still trivial. Chroma stays as a documented fallback. |
| Embedding | **Doubao-embedding-vision** (per PDF, no API key — we use the same Doubao key) for text, **CLIP (`open_clip` ViT-B/32)** for product images on A100 | Vision embedding bonus path: 4.2 "拍照找货" needs an image encoder. We index product images once on the A100 and reuse the vectors. |
| LLM | **Doubao-Seed-2.0-lite** (provided), endpoint `https://ark.cn-beijing.volces.com/api/v3/`, model id `ep-20260514111645-lmgt2`, key shared via private `.env` only (NEVER committed) | Limit: 80万 TPM / 700 RPM. Sufficient for development. |
| Dev IDE | TRAE (recommended by organizer) for Python; Xcode 15+ for iOS | TRAE is the org-blessed tool, but everyone uses what they prefer; Xcode is non-negotiable for iOS. |

**Bonus features we will commit to (pick 2, go deep — per the PDF rubric):**
- **4.3 conversational depth**: multi-turn memory + negation/exclusion ("不要含酒精的") + 2-3 product comparison. This is a natural fit for the RAG track Tujie owns.
- **4.2 multimodal — 拍照找货 (photo-to-product)**: leverages our A100 to pre-index product images via CLIP. The iPhone 13 camera makes a strong live demo. I (Shufeng) own the iOS image-picker + upload side; Tujie owns the vision-retrieval side.

Voice/TTS, shopping-cart CRUD, ordering — explicitly **out of scope v1**. Stub buttons only if a basic loop is solid by day 12 (target 2026-06-03).

---

## Repository Structure

Created at `~/Desktop/rag/AAALion-/`:

```
AAALion-/
├── README.md                  # Team-facing onboarding; quickstart for each role
├── LICENSE                    # MIT (or whatever Sam picks)
├── .gitignore                 # Python, Swift, macOS, .env, build artifacts, large data
├── .env.example               # Doubao key placeholder, qdrant URL, ports
│
├── client/                    # iOS app (Shufeng owns)
│   └── AAALionApp/
│       ├── AAALionApp.xcodeproj
│       ├── AAALionApp/
│       │   ├── App.swift
│       │   ├── Models/        # Message, ProductCard, ChatDelta (Codable)
│       │   ├── Services/      # ChatService (SSE), ProductService
│       │   ├── ViewModels/    # ChatViewModel (@Observable)
│       │   ├── Views/         # ChatView, MessageBubbleView, ProductCardView, ProductDetailView
│       │   └── Assets.xcassets
│       └── README.md          # How to open in Xcode, configure backend URL
│
├── server/                    # FastAPI backend (Sam owns)
│   ├── app/
│   │   ├── main.py            # FastAPI app + SSE endpoint /chat/stream
│   │   ├── routes/            # /chat, /products, /health
│   │   ├── services/          # doubao_client.py, rag_client.py
│   │   ├── schemas/           # pydantic models matching iOS Codable structs
│   │   └── config.py          # reads .env
│   ├── requirements.txt
│   ├── Dockerfile             # private-deployment requirement from PDF
│   ├── docker-compose.yml     # FastAPI + Qdrant
│   └── README.md
│
├── rag/                       # Retrieval / index (Tujie owns)
│   ├── ingest/
│   │   ├── chunk.py           # split product JSON → marketing_description / faq / reviews chunks
│   │   ├── embed_text.py      # Doubao-embedding-vision for text
│   │   └── embed_image.py     # CLIP on A100 for product images
│   ├── retrieve/
│   │   ├── query.py           # top-k with filters (category, price range, brand-exclude)
│   │   └── rerank.py          # optional rerank pass
│   ├── prompts/
│   │   └── system.md          # the "only answer from retrieved products, no hallucination" prompt
│   ├── eval/
│   │   └── golden.jsonl       # 20-30 test queries with expected product ids
│   ├── requirements.txt
│   └── README.md
│
├── data/                      # Toy dataset committed; large/raw gitignored
│   ├── README.md              # Where data lives, schema, how to extend
│   ├── seed/                  # Unzipped ecommerce_agent_dataset (100 products, 4 categories)
│   │   ├── 1_美妆护肤/
│   │   ├── 2_数码电子/
│   │   ├── 3_服饰运动/
│   │   └── 4_食品生活/
│   └── extra/                 # gitignored — extended scraped data goes here
│
├── docs/                      # Internal team docs
│   ├── ARCHITECTURE.md        # End-to-end system design with diagram refs
│   ├── PIPELINE.md            # How to develop / test / iterate (this is the team SOP)
│   ├── HARDWARE.md            # Devices, OS, A100 SSH-UC role, future Mac mini M4
│   ├── POLICY.md              # Persistent preferences (the "policy" file)
│   ├── ROADMAP.md             # 20-day day-by-day plan with owners
│   ├── DATA.md                # Schema, where-to-find-more-data prompts for Perplexity/Gemini
│   ├── API.md                 # OpenAPI-style spec of /chat/stream and /products endpoints
│   ├── FUTURE_WORK.md         # Stretch ideas categorized per person
│   └── meeting_template.md    # Used by meetings/
│
├── meetings/                  # Meeting notes (one file per meeting)
│   ├── 2026-05-20-kickoff.md  # Imports the existing 05-20 summary
│   └── README.md              # Naming convention: YYYY-MM-DD-topic.md
│
├── screenshots/               # Output of tools/screenshot_watcher.py — gitignored
│   └── .gitkeep
│
├── tools/                     # Helper scripts (committed)
│   ├── screenshot_watcher.py  # macOS pasteboard → ~/Desktop/rag/AAALion-/screenshots/
│   ├── ssh_a100.sh            # Helper to ssh into uc, cd into shufeng/
│   └── README.md
│
└── .github/                   # (if remote works) PR template, simple lint CI
    └── pull_request_template.md
```

**Notes on this layout**:
- The competition PDF says directory structure must include `/client`, `/server`, `/docs` — we satisfy that and split out `/rag` so Tujie has a clean ownership boundary.
- `.gitignore` will exclude: `__pycache__/`, `.venv/`, `.env`, `*.xcodeproj/xcuserdata/`, `DerivedData/`, `node_modules/`, `screenshots/*` (except `.gitkeep`), `data/extra/`, `*.qdrant`, `*.chroma_db/`.
- `data/seed/` IS committed so anyone cloning gets a runnable demo. The zip is 9.4 MB — fine for git.

---

## Screenshot Capture Script (`tools/screenshot_watcher.py`)

**Mechanism**: Poll macOS NSPasteboard via `pyobjc`. The user's shortcut `shift+ctrl+command+4` writes screenshots to the pasteboard (not to disk), so file-watching the Desktop is useless here.

**Behavior**:
1. Read `NSPasteboard.generalPasteboard()` `changeCount()` every 250 ms.
2. When `changeCount` increments AND the pasteboard contains `NSPasteboardTypePNG` or `NSPasteboardTypeTIFF`, save a copy.
3. Filter out non-screenshot images: if pasteboard also has `NSPasteboardTypeFileURL`, skip (likely a finder copy). Screenshots have no file URL.
4. Filename: `screenshot_YYYYMMDD_HHMMSS.png`. On same-second collision, append `_{changeCount}` for determinism.
5. Save target: `~/Desktop/rag/AAALion-/screenshots/`. Print absolute saved path to stdout.
6. TIFF → PNG conversion via `NSBitmapImageRep.representationUsingType_properties_` if needed.
7. Manual run: `python tools/screenshot_watcher.py`. Ctrl+C to stop. No launchd, no auto-start (per user choice).

**Dependencies**: only `pyobjc-framework-Cocoa` (already on system Python on macOS, or `pip install pyobjc-framework-Cocoa`). No Accessibility/Screen-Recording permission needed — pasteboard reads are TCC-light. The user may see a one-time "Python wants to paste from other apps" prompt; allow it once.

**Reference**: this approach was validated by the Plan agent; alternatives (pynput keystroke interception, `defaults write com.apple.screencapture location`) were rejected because the chosen shortcut is the clipboard variant.

---

## Documentation Strategy (for Sam + Tujie)

Each of the docs below is written for the team — they pick up cold and know what to do.

**`docs/PIPELINE.md`** (the developer SOP):
- **How to develop**: local dev order — Tujie ingests data + brings Qdrant up → Sam wraps it with FastAPI + Doubao → Shufeng builds the iOS chat against the SSE endpoint. Each role can stub the others with a mock during early-stage parallel work (Sam returns a static SSE stream from a JSON fixture; Tujie returns the top-3 products for any query).
- **How to test**: three layers.
  1. **RAG eval**: `rag/eval/golden.jsonl` — 20-30 queries with expected product ids. CI-able with `python -m rag.eval`.
  2. **Backend integration**: `pytest` against a live Qdrant + a mocked Doubao client (no real API calls in tests).
  3. **iOS**: XCTest for `ChatService` SSE parsing; manual UI smoke test on the iPhone 13 + Xcode simulator.
- **How to iterate**: small PRs, mandatory PR description with "what changed / how to test". Sam reviews backend, Tujie reviews RAG, Shufeng reviews iOS. PRs into `main`; each owner's personal branch (`shufeng`, `sam`, `tujie`) for in-flight work.
- **Local run quickstart** (5 commands):
  ```
  docker compose up -d            # Qdrant
  cd rag && python -m ingest      # one-time, indexes data/seed
  cd server && uvicorn app.main:app --reload
  open client/AAALionApp/AAALionApp.xcodeproj
  python tools/screenshot_watcher.py  # optional, in another terminal
  ```

**`docs/HARDWARE.md`**:
| Device | Owner | OS | Role |
|---|---|---|---|
| MacBook (Shufeng's) | Shufeng | macOS 15+ | Primary dev machine, Xcode, iOS simulator, local backend |
| iPhone 13 | Shufeng | iOS 17+ | Real-device testing, camera for 拍照找货 demo |
| Mac mini M4 (planned) | Team | macOS 15+ | Considered for shared dev/demo machine. Note: M4 unified memory makes it viable as a CI runner for the iOS build. Not blocking. |
| A100 (SSH UC) | Team | Linux | Heavy: CLIP image embedding (one-shot index build), batch RAG eval. Lives in `~/shufeng/AAALion-/` — a **new sibling** of the existing `~/shufeng/gpu-fuzz/` (which is a different, ongoing task — DO NOT touch it). Nothing outside `~/shufeng/` is ever modified. |

**SSH instructions for A100** (`tools/ssh_a100.sh` skeleton; user fills in hostname/user):
```bash
# Layout on the A100 (preserves existing gpu-fuzz/):
#   ~/shufeng/
#   ├── gpu-fuzz/        <-- existing, DO NOT TOUCH
#   └── AAALion-/        <-- new, parallel to gpu-fuzz/
#
# Initial setup (one-time, by Shufeng):
ssh uc 'mkdir -p ~/shufeng/AAALion- && ls ~/shufeng/'   # confirm gpu-fuzz still present
ssh uc 'cd ~/shufeng/AAALion- && git clone <remote> .'  # or rsync from MacBook if private
# Day-to-day:
./tools/ssh_a100.sh  # ssh uc -t 'cd ~/shufeng/AAALion- && exec $SHELL'
```
**Hard rule**: every command issued on the A100 must target a path under `~/shufeng/AAALion-/`. Never `cd` out of that subtree. Never run `pip install --user` (pollutes shared env) — only into the project `.venv`.

**`docs/POLICY.md`** (the persistent local-but-shared file):
- This is the file the user asked for: "every time from now, after I said something to be store in policy, it should be in local file, if something is proper to share with my teammate, dont put that into .gitignore, just share it on remote repo".
- Initial entries:
  - "Project is AI 全栈挑战赛, not 工程训练营."
  - "Shufeng owns iOS; Sam owns backend; Tujie owns RAG."
  - "Doubao API key is shared via private `.env`. Never committed. Never sent to iOS client."
  - "Most later development happens on `shufeng` branch to protect `main` stability."
  - "A100 work lives ONLY in `~/shufeng/...`; never touch `gpu-fuzz/`."
- POLICY.md is **committed and pushed** (shared with teammates).
- An additional `docs/POLICY_LOCAL.md` is gitignored, for entries the user marks as private.

**`docs/ROADMAP.md`** — 20-day plan:

| Date | Shufeng (iOS) | Sam (backend) | Tujie (RAG) | Joint |
|---|---|---|---|---|
| 05-22 → 05-24 | Xcode project, ChatView skeleton, mock SSE | FastAPI skeleton, `/chat/stream` returning fixture tokens | Unzip data, Qdrant up, ingest text chunks | Repo scaffolded |
| 05-25 → 05-28 | Real SSE wired, message bubbles, product card view | Doubao integration, real streaming, error handling | Top-k retrieval working, golden eval seeded | First end-to-end demo (text-only) |
| 05-29 → 06-01 | Image picker, multi-turn UI polish | Multi-turn context window | Negation / exclusion handling, comparison prompts | Multi-turn working |
| 06-02 → 06-05 | 拍照找货 client side, upload | Image upload endpoint, image-embed call | CLIP image index on A100, vision retrieval | Photo-search end-to-end |
| 06-06 → 06-08 | UI polish, animations, demo script | Caching, rate-limiting, hardening | Eval rerun, prompt tuning | Demo video record |
| 06-09 → 06-10 | Buffer: bug fixes, defense prep | Buffer | Buffer | Submit |

**`docs/FUTURE_WORK.md`**:
- *Shufeng / iOS*: voice input (Speech framework), TTS playback, shopping-cart UI, on-device CoreML rerank, widget extension for "今日推荐".
- *Sam / backend*: rate limiting with Redis, observability (Prometheus), retry/backoff for Doubao, gRPC for client (post-competition).
- *Tujie / RAG*: hybrid sparse+dense (BM25 + vector), learned reranker, query rewriting, recall@k benchmarking.
- *Joint*: A/B test infra, user-session telemetry, deployable Docker stack for judges to spin up locally.

---

## Where to Find More Data — REAL Data Only

**Important**: the bundled 100-product zip has been confirmed by the recruiters as AI-generated. We must replace or extend it with **real** product data for the demo and eval to be credible. The prompts below explicitly steer Perplexity / Gemini toward **discovering existing public sources**, not synthesizing more fake JSON.

1. **Prompts for Perplexity (search-first, gives sources)**:

   *Discover real, downloadable Chinese e-commerce datasets*:
   > "Find publicly available datasets of real Chinese e-commerce product listings from Taobao, JD.com, Tmall, or Pinduoduo that include: product title, brand, category, price, main image URL, and ideally marketing description + user reviews. Limit to datasets released 2022 or later. For each dataset, give me: name, host (HuggingFace / Kaggle / GitHub / academic site), license, size, exact download URL, and a one-sentence note on quality (curated vs. scraped). Prioritize ones that include images and ones whose license permits research use. Do not invent datasets — only list ones with a verifiable URL."

   *Discover real product APIs and scrape-friendly endpoints*:
   > "List public or semi-public APIs that return real product data for Chinese e-commerce (Taobao Open Platform, JD Union, Pinduoduo Open Platform, Xiaohongshu Notes API, Douyin commerce API). For each: auth requirement, rate limit, whether sandbox/test data is real or synthetic, and whether a college student team can register without a business license. Include any unofficial mirrors or aggregated datasets on GitHub (e.g., `xxx-spider`) that have current real data dumps."

   *Discover open multimodal product image datasets*:
   > "Find image-text product datasets useful for training or evaluating a multimodal e-commerce search system, with real product photos and Chinese text. Examples I'm looking to confirm or beat: M5Product, RPC, Products10K, Fashion-Gen. For each, give license, size, image count, whether Chinese text is present, and download URL. Add any newer (2024-2026) alternatives you can verify."

2. **Prompts for Gemini (deep extraction + structuring)** — once Perplexity returns candidate datasets, hand them to Gemini for normalization:
   > "Here is a dataset description and a sample row: [paste]. Write a Python script using `pandas` (or `datasets` for HuggingFace) that downloads it, filters to rows that have ALL of {title, brand, price (CNY), image_url, description longer than 100 chars}, and emits one JSON file per product matching this schema: [paste `p_beauty_001.json`]. Use real values from the dataset; do not fabricate FAQ entries — if real FAQ/reviews are not in the source, leave those fields empty."

3. **Sources to check directly** (the team should verify each, do not trust the names blindly):
   - HuggingFace search: `chinese ecommerce`, `taobao`, `product`. Likely hits include `Multimodal-Fatima/M5Product`, `BAAI/CCI`, vendor-specific dumps.
   - Kaggle: search "taobao", "jd.com", "tmall".
   - GitHub: `awesome-chinese-nlp` and `awesome-ecommerce-datasets` curated lists. Also active spiders (verify legality before running).
   - Academic: M5Product (CVPR 2022), Products10K, RPC (Retail Product Checkout).
   - Official: Taobao Open Platform, JD Union, Pinduoduo Open Platform — all require registration; check whether each team member's individual real-name verification is enough or whether a business entity is required.

4. **Fallback if all of the above fail or take too long**: keep the AI-generated zip as a **smoke-test** dataset only; build the demo around **30-50 real products manually curated** by the team (browse Tmall / JD, copy real titles + descriptions + images + reviews into the same JSON schema). Manual curation of 50 entries is ~4 hours total split three ways and produces unambiguously real data. This is the recommended floor.

5. **Adversarial test queries** (separate from product data — synthetic is fine here):
   > "Generate 30 challenging user queries a Chinese e-commerce shopper might ask an AI assistant, covering: (1) ambiguous intent ('便宜点的'), (2) negation ('不要含酒精的'), (3) multi-product comparison ('A 和 B 哪个更适合'), (4) budget + feature constraints ('预算500, 防水蓝牙耳机'), (5) follow-up refinement (multi-turn). Format as a JSON list of {query, expected_behavior} pairs."

---

## Git Workflow

After the scaffolding lands:

```bash
cd ~/Desktop/rag/AAALion-
git init
git add .
git commit -m "Initial scaffold: client/server/rag/docs/meetings/data + screenshot tool"
git branch -M main
git remote add origin https://github.com/YushengLiSam/AAALion-.git
git push -u origin main     # If access fails, leave local and report
git checkout -b shufeng
git push -u origin shufeng  # If access fails, leave local
```

- `main` is the stable branch. Direct pushes only for scaffolding / hotfixes; otherwise PRs from feature branches.
- Each developer owns a personal branch: `shufeng`, `sam`, `tujie`. Work-in-progress lives there.
- After this plan executes, the `shufeng` branch is set to match `main` exactly.

---

## A100 Setup (SSH UC)

After scaffolding, one-time on A100:
```bash
ssh uc
mkdir -p ~/shufeng/AAALion-  # explicit shufeng namespace — gpu-fuzz/ untouched
cd ~/shufeng/AAALion-
git clone https://github.com/YushengLiSam/AAALion-.git . || echo "private — copy via rsync from MacBook"
python3 -m venv .venv && source .venv/bin/activate
pip install -r rag/requirements.txt
# Verify GPU:
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The first heavy task: build the CLIP image index for all 100 products. Estimated <1 minute on A100. Output: `rag/data/image_index.qdrant` (gitignored; rsync'd back to laptops as needed).

---

## Verification — How We Know This Plan Worked

After execution, the user (Shufeng) can verify:

1. **Local repo exists**: `ls ~/Desktop/rag/AAALion-/` shows `client server rag docs meetings data tools README.md .gitignore`.
2. **Screenshot script works**: run `python ~/Desktop/rag/AAALion-/tools/screenshot_watcher.py`. Take a screenshot with `shift+ctrl+command+4`. A file named `screenshot_20260522_HHMMSS.png` appears under `screenshots/`.
3. **Remote pushed** (if accessible): `git -C ~/Desktop/rag/AAALion- ls-remote origin` lists `main` and `shufeng`.
4. **Shufeng branch matches main**: `git -C ~/Desktop/rag/AAALion- diff main shufeng` shows no diff.
5. **Policy file is readable**: `cat ~/Desktop/rag/AAALion-/docs/POLICY.md` shows initial entries; future `"please store X in policy"` requests append here.
6. **Hardware doc is current**: `docs/HARDWARE.md` mentions MacBook, iPhone 13, Mac mini M4 (planned), A100 SSH UC with `shufeng/` namespace.
7. **A100 namespace exists**: `ssh uc 'ls ~/shufeng/AAALion-/'` returns the cloned scaffold; `ls ~/gpu-fuzz/` is unchanged.
8. **Teammates can onboard**: Sam and Tujie should be able to read `README.md` + `docs/PIPELINE.md` and have their environment running in <15 minutes.
9. **Documentation pass**: `docs/ARCHITECTURE.md`, `docs/FUTURE_WORK.md`, and the README clearly explain what was built, how it works, and what's next — for the audience of Sam + Tujie.

If any of these fails, the execution turn reports it explicitly rather than glossing over.

---

## Open Questions Deferred to Execution

- The exact GitHub access state (private invite accepted vs. not) — execution will attempt `git push`, and if it fails, will report and leave the local repo ready with the exact paste-able command the user provided.
- The SSH UC hostname / user — execution will `ssh uc` (assuming the user's `~/.ssh/config` already has it) and report. If it fails, `docs/HARDWARE.md` will leave a `TODO(shufeng)` line.
- `gh` CLI is NOT installed locally; execution will not install it without the user asking, and falls back to plain `git`.

---

# Round 2 Plan — Device Deploy + Multimodal + Demos (2026-05-22, end of day)

## Context

Bootstrap (Round 1, above) shipped successfully: repo live, backend with TokenRouter+Chroma+real LLM running, iOS simulator app rendering streaming Chinese replies + product cards. 6 commits clean, all attributed to `Shufeng Chen <shufeng.c.dev@gmail.com>`.

Round 2 closes the remaining demo gaps so the work is **competition-defensible**:
- Physical iPhone 13 Pro install (so the judge demo isn't simulator-only).
- Image input (`拍照找货`) since bonus 4.2 is one of our two committed tracks and our current app is text-only.
- A folder of screenshotted demo runs so teammates (asleep) can see the real state.
- A teammate-friendly deploy guide for when Sam/Tujie wake up and want to run it on their own iPhones.
- A README + new WeChat update comparing the PDF rubric vs what we've actually shipped.
- Honest answer to "can team name be anything?" (per the PDFs: no rule restricts it).

### Decisions confirmed via user (this turn)

- **Team ID**: user will read from Xcode → Signing & Capabilities → paste back. Execution starts with "ask user for Team ID", then proceeds.
- **Multimodal approach**: vision LLM via TokenRouter first (`claude-haiku-4-5` is multimodal). CLIP-based image-retrieval deferred to Round 3.
- **Demo coverage**: all six scenarios — basic, conditional filter, multi-turn, negation, comparison, photo upload.
- **Search results to ingest**: user ran the Perplexity prompts from `docs/DATA.md`; the outputs landed at `/Users/shufengc/Desktop/rag/search/` (3 markdown files, ~21 KB each). Plan ingests, renames, indexes them, and incorporates the findings into the data strategy.

### Honest read of the Perplexity research outputs

The three files deliver a clear verdict on Chinese e-commerce data availability:

- **MEP-3M** (3M products, 76 GB) — title + image only, **no price/brand/reviews**, university-only ToU.
- **JDsearch** (12M products) — fully anonymized tokens; not human-readable.
- **Products-10K** (10K SKUs, ~190K images) — images + category IDs only; **no price/brand text** in the released CSVs.
- **OpenBG-IMG / TAOBAO-MM / TMPS** — anonymized or knowledge-graph form; not a usable product catalog.
- **M5Product** (Chinese, 6M) — gated to academic researchers with signed commitment letter.
- **Fashion-Gen** — English only.
- **Official APIs** (Taobao / JD / Pinduoduo / Xiaohongshu / Douyin) — every commerce-grade endpoint requires a 营业执照 (business license). Individual student-team registration unlocks only basic affiliate / link-generation tiers, not real product catalogs.

**Conclusion** (which I'll surface in the WeChat update and `docs/research/README.md`): there is **no free, usable, real Chinese e-commerce dataset** with the full {title + brand + price + image URL + description + reviews} schema. The AI-generated seed in `data/seed/` is actually competitive with what's publicly available. Right move: **(a)** keep the AI-gen seed as the pipeline demo set, **(b)** hand-curate 10-15 real products from Tmall/JD to prove the pipeline handles real data, **(c)** document this constraint in the defense slides as "research effort surfaced + workaround chosen" — itself a +工程质量 signal.

## Plan (priority-ordered, executable)

### 1. Team name answer (informational, 1 line)

Per the two organizer PDFs in `/Users/shufengc/Desktop/rag/`, there is **no rule restricting team names**. "AAALion" or any reasonable name is fine. Recorded in `docs/POLICY.md` under §"Scope and identity".

### 1b. Ingest the Perplexity research outputs into the repo

Source: `/Users/shufengc/Desktop/rag/search/` — three files with mangled names (`> _Find image-text product datasets useful for.md` etc.) and an HTML-encoded `>` prefix from the Perplexity copy-paste.

Move + rename + index:

```
docs/research/
├── README.md                                            # NEW — index + the conclusion above
├── 2026-05-22-cn-ecommerce-datasets.md                  # was: _Find publicly available datasets of real Chinese.md
├── 2026-05-22-multimodal-ecommerce-benchmarks.md        # was: > _Find image-text product datasets useful for.md
└── 2026-05-22-cn-ecommerce-apis.md                      # was: > _List public or semi-public APIs that return.md
```

`docs/research/README.md` contains:
- The verdict (no free, usable real dataset; APIs need 营业执照).
- A one-table summary cross-linking each file.
- The "hand-curate 10-15 products" fallback decision and a checklist of which categories need real products to anchor the demo.
- Cross-link to `docs/DATA.md` (which already documents the search prompts).

Then drop a back-link from `docs/DATA.md` ("see `docs/research/` for the actual results").

This makes the research visible to Sam/Tujie on the remote without them having to dig through Shufeng's local search/ folder.

### 2. Device deploy to iPhone 13 Pro

1. Wait for user to paste their 10-char Team ID (the only manual input).
2. Set `DEVELOPMENT_TEAM: <id>` in `client/AAALionApp/project.yml` under `targets.AAALionApp.settings.base`. (xcodegen supports this; field already exists with empty value — `project.yml` line 14.)
3. Regenerate `.xcodeproj` via `aaalion ios`.
4. Build for device with `xcodebuild ... -destination 'platform=iOS,id=7310469E-E396-5197-9408-FF1AD58D4CF2' -allowProvisioningUpdates`. Xcode will auto-download a development cert + provisioning profile bound to that Team.
5. Install via `xcrun devicectl device install app --device <UUID> <.app path>`.
6. Launch app on the device, ask user to verify it appeared on the home screen.
7. First launch on iPhone triggers Settings → General → VPN & Device Management → trust prompt; user does this once.

If `xcodebuild` reports any provisioning error after the Team is set, execution reports verbatim and asks the user for one more click.

### 3. Image input in iOS + backend multimodal route

#### iOS additions
- `client/AAALionApp/AAALionApp/Views/ChatView.swift`: add a "📎" button in the composer HStack next to the TextField. On tap, presents `PhotosPicker` (iOS 17+). The picked image is held in `viewModel.pendingImage: UIImage?` and previewed as a small thumbnail above the input field. Tapping the thumbnail clears it.
- `client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift`: add `pendingImage: UIImage?` (or `Data?` for the raw bytes). When `send()` runs and an image is attached, the user message bubble shows the image inline. The image is JPEG-compressed (quality 0.7, max 1024px long edge) before upload.
- `client/AAALionApp/AAALionApp/Services/ChatService.swift`: extend `WireMessage` to carry an OpenAI-style content array when an image is present: `[{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}]`. Plain-text messages keep the existing `content: String` shape (backward-compatible).
- `client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift`: render attached image inline above the text bubble when `message.image != nil`.

#### Backend additions
- `server/app/schemas/chat.py`: change `ChatMessage.content` from `str` to `str | list[ContentPart]` where `ContentPart` is a discriminated union (`text` or `image_url`). Pydantic v2 handles this cleanly.
- `server/app/routes/chat.py`: when assembling the message history for the provider, pass content through unchanged (vision-capable models already understand the OpenAI multimodal content array). If retrieval over text was needed to find candidates, use the text part for embedding; if only image was sent, use `"<photo upload>"` as the embed query (returns category-balanced top-k via Chroma).
- `server/app/services/llm_provider.py`: no change needed — `OpenAICompatibleProvider` already passes content through to `chat.completions.create`. Confirm `TOKENROUTER_MODEL=claude-haiku-4-5` supports vision (verified earlier: it does).

Skip a separate `/chat/multimodal` route. Single `/chat/stream` accepts both shapes via the union schema. Cleaner contract.

### 4. Teammate deploy guide

New file: `docs/DEPLOY_GUIDE.md`. Audience: 李雨晟 / 管图杰 with their own MacBooks and iPhone ≥13. Sections:

1. Prerequisites (Mac, Xcode 15+, Homebrew, Apple ID).
2. Install: `brew install xcodegen`, clone the repo, `make install-cli` (or copy the `aaalion` script).
3. Get a TokenRouter key (link to the console) OR use `LLM_PROVIDER=echo` for UI-only work.
4. Backend: `python3.12 -m venv .venv`, `pip install -r server/requirements.txt`, `cp ../.env.example .env`, `aaalion ingest`, `aaalion backend`.
5. iOS simulator: `aaalion ios-sim`.
6. iOS device: Xcode → Settings → Accounts → add Apple ID; in `project.yml` set `DEVELOPMENT_TEAM` to their own team ID; `aaalion ios-device`; `xcrun devicectl device install app …`. iPhone must be paired via USB at least once.
7. Troubleshooting: 5 common errors observed today (signing, env not loading, SSE parser hang, telemetry warnings, .xcodeproj regen wiping team).

References existing docs (`IOS_SETUP.md`, `HARDWARE.md`) instead of duplicating their content.

### 5. Demo recording

New folder: `docs/demos/2026-05-22/` (kebab-case, no "results" suffix — the folder IS the results). Contents:

- `01-basic-recommendation.png` + `01-basic-recommendation.md` — query `推荐一款适合油皮的洗面奶`, expected: structured recommendation + ≥1 cleanser product card.
- `02-conditional-filter.png` + `.md` — query `200元以下的蓝牙耳机有哪些`, expected: products ≤ ¥200, electronics category.
- `03-multi-turn.png` + `.md` — first query `推荐一款适合油皮的洗面奶`, then `再便宜点的呢`, expected: cheaper alternative grounded in the catalog.
- `04-negation.png` + `.md` — query `推荐防晒霜，但我不要含酒精的，也不要日系品牌`, expected: sunscreens that aren't from Japanese brands.
- `05-comparison.png` + `.md` — query `雅诗兰黛小棕瓶和兰蔻小黑瓶哪个更适合熬夜`, expected: structured A-vs-B comparison.
- `06-photo-upload.png` + `.md` — upload `data/seed/1_美妆护肤/images/p_beauty_001_live.jpg` with text `这款多少钱`, expected: identifies it as 雅诗兰黛小棕瓶, returns ¥720 from the indexed JSON.
- `README.md` — index page summarizing the 6 demos with thumbnails and the "what we tested vs. what worked" verdict.

Each sidecar `.md` includes: the user query, the raw SSE log (truncated), the assistant's full reply text, the rendered product card IDs, and a single-word verdict (PASS / WEAK / FAIL with a one-line note).

Driven via `xcrun simctl launch booted com.aaalion.lionpick -test-query "..."`. For the photo case, extend the launch-arg harness to support `-test-image <path>` that loads the image at startup. Screenshot taken after streaming completes (sleep ~12s per scenario).

**Iterate-until-good loop**: if any demo's verdict isn't PASS, execution adjusts (in priority order): (a) tighten `rag/prompts/system.md`, (b) tweak `rag/retrieve/query.py` filters or top-k, (c) escalate the LLM model from `claude-haiku-4-5` to `anthropic/claude-opus-4.7` (also on TokenRouter) for the failing scenario. Re-run, re-screenshot, document the fix in the sidecar `.md`'s "Iterations" section.

Folder commits + pushes to `main` and `shufeng`.

### 6. README + WeChat update

- `README.md`: update "Quickstart" to use `aaalion` commands; add a "Status" table that mirrors the demo screenshots ("Basic ✓ Conditional ✓ Multi-turn ✓ Negation ✓ Comparison ✓ Photo ✓"); link to `docs/demo-results/` for proof.
- `docs/WECHAT_UPDATE_2026-05-23.md`: Chinese, paste-ready, structured as **PDF requirement → our state → screenshot link**. Aligns to the PDF's four scoring buckets:
  - 基础功能完整性 (35%) — done; cite screenshots.
  - 工程质量 (25%) — current state, gaps (no auth, no rate limit, no observability).
  - 效果与可靠性 (20%) — Chroma recall@k from `aaalion eval`, hallucination check from prompt grounding.
  - 加分项 (20%) — 4.3 (multi-turn / negation / comparison) ✓, 4.2 (vision LLM ✓, CLIP-based ⏳).

  Closes with a clear ask list for Sam (real Doubao key, performance hardening) and Tujie (CLIP indexer, golden eval expansion).

## Critical files to modify (representative list)

```
client/AAALionApp/project.yml                                  # DEVELOPMENT_TEAM
client/AAALionApp/AAALionApp/Views/ChatView.swift              # PhotosPicker UI
client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift     # inline image render
client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift    # pendingImage state, -test-image launch arg
client/AAALionApp/AAALionApp/Services/ChatService.swift        # content[] payload
client/AAALionApp/AAALionApp/Models/Message.swift              # add image: Data?
server/app/schemas/chat.py                                     # content union (str | list[ContentPart])
server/app/routes/chat.py                                      # multimodal message assembly
README.md                                                      # quickstart refresh + status table
docs/DEPLOY_GUIDE.md                                           # NEW — teammate onboarding
docs/WECHAT_UPDATE_2026-05-23.md                               # NEW — PDF-vs-done summary
docs/research/README.md                                        # NEW — index + verdict on data availability
docs/research/2026-05-22-cn-ecommerce-datasets.md              # NEW — renamed from search/
docs/research/2026-05-22-multimodal-ecommerce-benchmarks.md    # NEW — renamed from search/
docs/research/2026-05-22-cn-ecommerce-apis.md                  # NEW — renamed from search/
docs/DATA.md                                                   # back-link to docs/research/
docs/demos/2026-05-22/{01..06}.{png,md} + README.md            # NEW — recorded demos + index
docs/commits/20260522-007-*.md                                 # NEW — major-commit record
```

## Reuse from existing code

- `tools/aaalion` for run-from-anywhere commands.
- `Makefile` `ios-sim` + `ios-device` targets (extend `ios-device` to chain `devicectl install`).
- `xcrun simctl launch -test-query` harness in `ChatViewModel.runScriptedQueryIfAny()` — extend it to `runScriptedImageQueryIfAny()` for the photo demo.
- `tools/check-secrets.sh` — runs before each push.
- `tools/screenshot_watcher.py` is NOT used for demo capture (that's for the design loop); use `xcrun simctl io booted screenshot` instead.
- `rag/retrieve/query.py` `Filter` already supports `brand_exclude` and `price_max` — the comparison/negation demos exercise this.

## Verification (how we'll know it worked)

1. `xcrun devicectl device install app …/狮选.app` returns success and the app launches on the iPhone 13 Pro (user confirms visually).
2. User taps the 📎 button in the app, selects a photo, types text, sends → backend logs a 200 + assistant reply mentions image content.
3. `ls docs/demo-results/2026-05-22-evening/` shows 6 PNGs + 6 sidecar MDs, all with non-zero size.
4. Each demo MD's "verdict" line says "PASS" (or has an iterated-fix log if it took multiple tries).
5. `tools/check-secrets.sh` clean.
6. `git log --pretty='%h %an <%ae>' -10` shows all commits attributed to `Shufeng Chen <shufeng.c.dev@gmail.com>`.
7. `make sync-a100` succeeds; `ssh uc 'stat -c %y ~/shufeng/cuda-fuzzing'` shows mtime unchanged.
8. `README.md`'s status table renders correctly (preview the markdown).
9. `docs/WECHAT_UPDATE_2026-05-23.md` reads naturally to a Chinese reader (sanity check by length, structure, and grounding to the actual artifacts).

## Open question for execution

- **Multimodal request body shape**: TokenRouter is OpenAI-compatible, and the user is using `claude-haiku-4-5` whose underlying API can be addressed in either Anthropic or OpenAI shape per TokenRouter's `supported_endpoint_types`. Default to OpenAI shape (`content: [{type, text}, {type, image_url, image_url:{url}}]`) since `OpenAICompatibleProvider` already calls `client.chat.completions.create`. If the API rejects this, fall back to a `data:image/jpeg;base64,...` data-URL inside an `image_url` block. If still rejected, switch the provider to Anthropic-shape requests (TokenRouter supports both endpoint types for claude-haiku-4-5).
- **Device install user trust prompt**: the very first install on the iPhone needs the user to navigate Settings → General → VPN & Device Management → trust their cert. Cannot be automated. Surface this clearly during execution.

---

# Round 3 Plan — UX Polish + Real A100 + RAG Depth (2026-05-23)

## Context

User installed the app on iPhone 13 Pro and ran into 4 concrete problems + 5 capability gaps, all confirmed via screenshots:

1. **Product card images don't load** — `_image_url()` returns relative `/static/...` paths; iOS `AsyncImage` needs absolute URLs.
2. **Can't change backend URL at runtime** — hardcoded in `Config.swift`; have to rebuild when LAN IP changes.
3. **Can't edit a sent message** — ChatGPT/Claude both let you edit the last user prompt; we don't.
4. **Photo input is library-only** — no camera, no Files importer.
5. **App icon + color scheme are placeholder-grade** — user described them as "stupid"; explicit branding gap.

Plus three depth issues the user surfaced explicitly:

6. **Vision LLM hallucinated** — uploaded a candle, model said "beer". Reveals a real gap: we're doing image-as-content-to-vision-LLM, not CLIP-based visual retrieval.
7. **Catalog is narrow** — 100 products across only 4 categories (美妆/数码/服饰/食品). User searched "助孕用品" (reproduction aid); model honestly said no match, but the catalog truly has no maternity/health products. Recall feels bad because the breadth isn't there.
8. **A100 idle** — we shipped Round 2 with the A100 used as a sync target only. Plan promised CLIP image indexing on A100; we deferred. User explicitly called this out.

This round closes all 8 items in one sweep. User chose the "ship everything" scope at this turn's clarifier; defense is 19 days out which is enough runway if we don't introduce big new risks.

### Decisions confirmed via user (this turn)

- **Round 3 scope**: ship all 8 items (bugs + UX + visual + A100 CLIP + data expansion + voice + TTS + caching).
- **App icon**: try TokenRouter image-gen (e.g. `openai/gpt-5.4-image-2`) first; fall back to programmatic SF-Symbols-on-gradient if quality is poor.
- **"Integrate Claude design"**: call Claude (`anthropic/claude-opus-4.7` or `claude-haiku-4-5` via TokenRouter) with a description of the app + the current ugly state, ask for concrete hex codes, font choices, spacing scale, rationale. Implement what comes back as Swift `Color` + typography helpers.

## Phases (ordered for ship-ability)

### Phase A — Critical bug + Settings screen (~3 hrs)

**A1. Fix broken product card images.**
Backend `server/app/routes/chat.py:53-57` (`_image_url`) currently returns `f"/static/{image_path}"` (relative). iOS `Views/ProductCardView.swift:8` passes the relative `URL?` straight to `AsyncImage`, which fails. Fix on the iOS side: resolve relative URLs against `Config.backendURL` inside `ProductCard.imageURL` decode (or in the view). Keep the backend response as relative — it stays decoupled from any deployment hostname.

**A2. Settings screen with `UserDefaults` persistence.**
- New `Views/SettingsView.swift`: form with backend URL field (default to `Config.backendURL`), "Save" button persists to `UserDefaults.standard`.
- `Config.backendURL` resolution becomes: env var > UserDefaults `lionpick.backendURL` > hardcoded fallback.
- Add a gear button in `ChatView`'s navigation bar that opens `SettingsView` as a sheet.
- Auto-detect LAN backend on first launch (probe `/health` against the Mac's LAN IP range) — nice-to-have if simple, skip if it complicates the PR.

### Phase B — Chat UX expansions (~4 hrs)

**B1. Edit-last-message via context menu.**
- `MessageBubbleView`: `.contextMenu` on user-role messages. Options: "Edit", "Copy".
- `ChatViewModel.editMessage(id:)`: deletes the assistant reply that followed (if any) and the user message itself, sets `draft` to the old text, gives focus to the input.
- iOS standard pattern; matches ChatGPT/Claude behavior the user requested.

**B2. Camera capture.**
- New `Views/CameraPicker.swift`: `UIViewControllerRepresentable` wrapping `UIImagePickerController` with `.sourceType = .camera`.
- Composer button (camera icon, SF Symbol `camera`) presents it as a sheet. Captured `UIImage` → JPEG data → `viewModel.pendingImage`.
- Info.plist already has `NSCameraUsageDescription` — works as-is.

**B3. Files importer.**
- New `Views/FilePicker.swift`: `.fileImporter(isPresented:allowedContentTypes:[.image, .jpeg, .png])` modifier on the composer.
- Composer button (folder icon, SF Symbol `folder`) toggles it. Picked file → load data → `viewModel.pendingImage`.
- Three image-source buttons total: 📷 camera, 🖼️ photos, 📁 files. Wrap in a single Menu(...) if the composer gets crowded.

### Phase C — Visual / branding (~3 hrs)

**C1. Ask Claude (via TokenRouter) to spec the palette + typography.**
- One-shot call from `tools/design_consult.py` (new helper): sends a prompt describing the app + competition context + current state ("see attached screenshot"), asks for a JSON response with `palette` (background / surface / text-primary / text-secondary / accent / accent-muted), `typography` (font, sizes for chat-bubble / title / caption), and `rationale` (~3 sentences). Saves the JSON to `client/AAALionApp/design-tokens.json` (committed reference).
- Manual review of the JSON before applying.

**C2. App icon via TokenRouter image generation.**
- `tools/generate_icon.py`: requests a 1024×1024 icon from `openai/gpt-5.4-image-2` (or whichever image model TokenRouter exposes — verify via `/v1/models`). Prompt: "minimalist mobile app icon, geometric stylized lion head with a small shopping bag accent, flat design, warm off-white background, modern e-commerce shopping-assistant app, no text, vector look". Saves to `client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/`.
- Generate the required iOS icon sizes (1024, 180, 167, 152, 120, 87, 80, 76, 60, 40, 29, 20) by resizing the 1024 source via `sips`.
- If the generated image is unusable, fall back to programmatic SF-Symbol-on-gradient (`lion.head` or `figure.2.right.holdinghands` doesn't exist; use `pawprint.fill` on a warm gradient).

**C3. Implement the design tokens in Swift.**
- New `Views/Theme.swift`: `Color.appBackground`, `Color.appSurface`, `Color.appAccent`, `Color.appTextPrimary`, `Color.appTextSecondary`, plus a typography helper `Font.appBody`, `Font.appCaption`, `Font.appTitle`.
- Apply across `ChatView`, `MessageBubbleView`, `ProductCardView`, `ProductDetailView`, `SettingsView`.
- Polish: skeleton placeholders for product card images while loading, empty-chat illustration ("👋 你好 / Hello — try asking…"), smooth send-button animation, scroll-to-bottom button when user scrolls up.

### Phase D — A100 actually used for CLIP visual retrieval (~6 hrs)

This addresses the candle→beer hallucination directly.

**D1. Install CUDA-matched torch on `uc` (no driver touch).**
```
ssh uc
cd ~/shufeng/AAALion-
source .venv/bin/activate || python3 -m venv .venv && source .venv/bin/activate
pip install torch==2.4.1 --index-url https://download.pytorch.org/whl/cu124
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
# If False → fall back: pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install open_clip_torch pillow chromadb
```
If neither works (driver/library mismatch is too deep), use CPU torch — 100 images × 512-d ViT-B/32 takes ~30 sec on CPU, acceptable.

**D2. CLIP image index build script.**
- `rag/ingest/embed_image.py` (replace stub with real impl): walks `data/seed/*/images/*.jpg`, batches through OpenCLIP `ViT-B-32` with `laion2b_s34b_b79k` weights, returns `[(product_id, 512-d vector)]`.
- `rag/ingest/run_image.py`: drives the above, upserts into Chroma `products_image` collection with payload `{product_id, category, brand, base_price}`.
- Run on `uc` over SSH: `ssh uc 'cd ~/shufeng/AAALion- && source .venv/bin/activate && python -m rag.ingest.run_image'`.
- Rsync the resulting `data/.chroma/` back to MacBook (or run the backend on uc and tunnel — see D4).

**D3. Retrieve by image vector in `rag/retrieve/query.py`.**
- New `query_image(image_bytes: bytes, k: int = 5) -> list[Hit]`: embed the uploaded image via the same OpenCLIP model, query Chroma `products_image`, return product dicts.

**D4. Wire image-first retrieval in the backend.**
- `server/app/routes/chat.py`: when `_has_image(req.messages)` is true, extract the base64 image, call `rag.retrieve.query_image(...)` for top-3 visually similar products, build the catalog block from those, THEN call the LLM with the image + the visually-grounded catalog. The LLM now has "the image likely matches one of these 3 — confirm and recommend" rather than "guess what the image is".
- For the demo: re-run demo 06 (or a new "candle" demo) — should now correctly retrieve the closest catalog item by visual similarity, dropping the candle-as-beer hallucination.

**D5. Optional: run OpenCLIP on `uc` as a service.**
If GPU is working on uc, expose a `/embed-image` HTTP endpoint on the uc machine (FastAPI on a separate port), tunneled to the Mac via SSH `-L 9000:localhost:9000`. The Mac backend POSTs uploaded images here. Keeps GPU utilization on uc instead of CPU on the Mac. Deferred to a v2 if D1-D4 ship clean.

### Phase E — Data depth: 4 new categories (~4 hrs)

User's "reproduction aid" probe showed the catalog is too narrow. Add 4 new categories so semantic search has somewhere to land:

| New category | ~25 products | Coverage examples |
|---|---|---|
| `5_母婴用品` | maternity + baby | 备孕营养品, 孕妇护肤, 奶粉, 婴儿用品 |
| `6_健康保健` | health + pharmacy | 维生素, 中药调理, 助眠产品, 蛋白粉 |
| `7_家居家具` | home + decor | 蜡烛, 家具, 餐具, 香薰 (← would catch the "candle" image case) |
| `8_户外运动` | outdoor + sports | 帐篷, 登山鞋, 运动手表, 自行车 |

Process:
- `tools/generate_products.py`: calls TokenRouter `anthropic/claude-opus-4.7` with our exact schema + the 4 category prompts. Produces 25 JSON files per category that match the existing seed schema. Honest disclaimer: this is still LLM-generated data — but now diverse + broader.
- Image sources: try TokenRouter image-gen for each product image (1024×1024 generic shots) OR scrape Wikimedia / use placeholder. Probably mixed.
- Drop into `data/seed/5_母婴用品/`, `data/seed/6_健康保健/`, etc.
- Re-run `aaalion ingest` — Chroma index goes from 992 to ~2000 chunks.

Real-product manual curation (10-15 real items from Tmall/JD) stays the user's task per Round 2 plan — surfaced in WeChat update.

### Phase F — Voice input + TTS + Caching (~6 hrs)

**F1. Voice input (4.2 bonus ⭐).**
- iOS `Speech` framework. New `Services/SpeechService.swift`: wraps `SFSpeechRecognizer` for Chinese (`zh_CN`). Tap mic button → start recognition → stream transcribed text into `viewModel.draft` → tap stop or auto-stop on silence.
- New composer button (SF Symbol `mic`, swaps to `mic.fill` while recording).
- Info.plist needs `NSSpeechRecognitionUsageDescription` and `NSMicrophoneUsageDescription` — add via project.yml.

**F2. TTS (4.2 bonus ⭐⭐).**
- iOS `AVSpeechSynthesizer`. New `Services/TTSService.swift`: speak any `String` in `zh-CN`.
- "Speaker" button on each assistant `MessageBubbleView` — tap to read the message aloud.

**F3. Hot-query cache in backend (4.4 bonus ⭐).**
- New `server/app/services/cache.py`: in-memory `dict` with LRU eviction (1000 entries, 10-min TTL). Key: `sha256(system_prompt + json(messages) + image_sha256)`. Value: the full SSE event list to replay.
- `routes/chat.py`: check cache before calling LLM. On hit, replay the events with a small per-token delay so the stream still feels live.
- This drastically lowers cost during demo (judges asking the same query twice).

**F4. First-token latency budget (4.4 bonus ⭐⭐).**
- Add timing instrumentation: log `t_request_received`, `t_retrieval_done`, `t_first_llm_delta`, `t_done` per request.
- iOS shows a "skeleton" assistant bubble (3 animated dots) until the first delta arrives — sub-second visual feedback.
- Target: <1 s from tap to first character. Documented in WeChat as the 4.4 ⭐⭐ deliverable.

### Phase G — Re-record demos + update docs (~2 hrs)

- Re-run all 6 demos with the new icon + design + fixed images.
- Add 4 new demos: edit-message, camera capture, CLIP-based candle-not-beer, expanded-category ("助孕用品" now hits 母婴 products).
- Update `README.md` status table.
- New WeChat update `docs/WECHAT_UPDATE_2026-05-24.md`.
- Major-commit record `docs/commits/20260523-010-round3.md`.
- Add `docs/RUBRIC_MAPPING.md`: explicit table mapping each PDF §4 sub-item to a code/demo artifact (anti-handwave defense for the judges).

## Critical files

```
# Phase A
server/app/routes/chat.py                         # _image_url: keep relative
client/AAALionApp/AAALionApp/Models/ProductCard.swift   # resolve relative against Config.backendURL
client/AAALionApp/AAALionApp/Config.swift         # add UserDefaults lookup
client/AAALionApp/AAALionApp/Views/SettingsView.swift   # NEW
client/AAALionApp/AAALionApp/Views/ChatView.swift # gear button → SettingsView sheet

# Phase B
client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift  # contextMenu
client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift # editMessage(id:)
client/AAALionApp/AAALionApp/Views/CameraPicker.swift   # NEW
client/AAALionApp/AAALionApp/Views/FilePicker.swift     # NEW (wraps .fileImporter)

# Phase C
tools/design_consult.py                           # NEW (Claude → tokens)
tools/generate_icon.py                            # NEW (TokenRouter image-gen)
client/AAALionApp/design-tokens.json              # NEW (committed reference)
client/AAALionApp/AAALionApp/Views/Theme.swift    # NEW (Color/Font helpers)
client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/  # NEW (icon set)

# Phase D
rag/ingest/embed_image.py                         # replace stub
rag/ingest/run_image.py                           # NEW
rag/retrieve/query.py                             # add query_image
server/app/routes/chat.py                         # image-first retrieval branch

# Phase E
tools/generate_products.py                        # NEW (Claude opus → 100 new JSONs)
data/seed/5_母婴用品/                              # NEW (25 items + images)
data/seed/6_健康保健/                              # NEW
data/seed/7_家居家具/                              # NEW (includes 蜡烛 / candles)
data/seed/8_户外运动/                              # NEW

# Phase F
client/AAALionApp/AAALionApp/Services/SpeechService.swift   # NEW
client/AAALionApp/AAALionApp/Services/TTSService.swift      # NEW
server/app/services/cache.py                                # NEW
server/app/routes/chat.py                                   # cache hook + latency log
client/AAALionApp/project.yml                               # NSSpeechRecognitionUsageDescription, NSMicrophoneUsageDescription

# Phase G
docs/demos/2026-05-23/                            # NEW (re-records + new scenarios)
docs/RUBRIC_MAPPING.md                            # NEW
docs/WECHAT_UPDATE_2026-05-24.md                  # NEW
docs/commits/20260523-010-round3.md               # NEW
README.md                                         # status table refresh
```

## Reuse from existing code

- `Config.backendURL` resolution chain — extend, don't replace.
- `ChatService.ChatRequest.Content` union — multimodal payloads already work.
- `top_k` in `rag_client.py` — pattern for adding `top_k_image`.
- `tools/check-secrets.sh` before each push.
- `tools/aaalion` wrapper for all make commands.
- `xcrun simctl launch ... -test-query/-test-image-url` harness for re-recording demos.
- `xcrun devicectl device install app` for iPhone deploys + `aaalion resign` for weekly refresh.

## Verification (end-to-end checks)

1. **Product images load on iPhone** — open 狮选 on iPhone 13 Pro, type "推荐一款洗面奶", confirm product card thumbnails render (not the placeholder photo icon).
2. **Settings screen works** — gear button in toolbar opens settings; change backend URL; save; next message uses new URL (test by intentionally pointing wrong, see error banner).
3. **Edit last message** — long-press user bubble → Edit → text reappears in composer → modify → send → previous assistant reply is discarded, new reply streams.
4. **Camera capture** — tap 📷 button, take a photo, photo appears as the staged image, send with text "这是什么?" — vision LLM responds.
5. **Files importer** — tap 📁, pick a saved JPEG, sends as image attachment.
6. **App icon visible** — 狮选 icon on iPhone home screen looks polished (not placeholder).
7. **Theme applied** — chat bubbles, send button, product cards all use the new palette consistently.
8. **CLIP retrieval** — upload candle image, top-3 retrieved products are home/decor items (蜡烛 if Phase E shipped), LLM no longer says "beer".
9. **Voice input** — tap mic, speak "推荐一款适合油皮的洗面奶", text appears in draft, tap send.
10. **TTS** — tap speaker icon on assistant bubble, Chinese voice reads the reply.
11. **Cache hit** — send the same query twice in a row, second response is noticeably faster (track via the latency log).
12. **Expanded categories** — ask "助孕用品" → 母婴 products surface (not random cosmetics).
13. **`docs/RUBRIC_MAPPING.md`** explicitly cites each §4 sub-item with the artifact that proves it.
14. **`tools/check-secrets.sh`** clean; all commits attributed to `Shufeng Chen <shufeng.c.dev@gmail.com>`; `cuda-fuzzing/` mtime on `uc` unchanged.

## Risks & deferrals

- **CUDA torch on `uc` may fail** even with cu124 wheel because the driver/library mismatch is at the kernel level. Mitigation: CPU torch fallback (30 sec for 100 images is fine). If CPU torch also fails, run OpenCLIP on the MacBook via Apple Silicon MPS backend.
- **TokenRouter image-gen may reject the prompt** or produce garbage. Mitigation: programmatic icon fallback in Phase C2.
- **Voice + TTS pull in two new Apple frameworks** (`Speech`, `AVFoundation`) — first time we cross these surfaces. Could eat half a day on entitlements/permissions. Schedule them last (Phase F) so the earlier deliverables ship even if F slips.
- **LLM-generated category expansion is still AI-data**. We're not making the rubric-defending case stronger here; we ARE making the demo more impressive. Document this honestly in the WeChat update.
- **Defense day cert refresh + LAN URL update** — Settings screen (A2) makes this trivial, but still requires the dev to remember to run `aaalion resign` weekly. Calendar reminder in `docs/RUBRIC_MAPPING.md`.

## Open question for execution

- **TokenRouter image-gen model name + endpoint shape**: verify with a probe call to `/v1/models` filtered to `image-generation` endpoint_types. If the model rejects PNG output or requires special params, log and fall back.
- **Whether to run the backend on `uc` over SSH tunnel** (Phase D5) or keep it on MacBook + call uc as a remote vector service. Cleaner ergonomics either way; default to "MacBook backend, uc as a one-off embed service" unless GPU shows clear win.

---

# Round 4 Plan — Docs + Team Proposal + File Bug Fix (2026-05-23)

## Context

User installed Round 3 on iPhone 13 Pro and tested. Results:
- ✅ Edit, Copy, voice input, photo upload (Photos + Camera), Settings — all work
- ❌ **Files importer returns an error** when picking from the Files app
- 📋 User asked for: (a) embed lion icon top-right in README, (b) comprehensive implementation docs, (c) WeChat update, (d) a forward-looking proposal plan committed to remote for **teammate review** (not solo execution)
- 🤔 User wants my honest take on this plan + a revised version

## My take on user's plan

**Agree on all five items.**

- Icon top-right: small cost, high identity payoff.
- Implementation docs: 22+ files have piled up across 3 rounds. A single index page (not duplication) helps a fresh reader.
- WeChat update: last one was 5-23 morning; Round 3 shipped a lot since.
- Proposal plan: smart — teammates need a forward roadmap to engage. Asking before executing is right.
- Don't execute the proposal: agree — Sam / Tujie buy-in is worth the wait.

**One addition**: also fix the **Files-importer bug** in this round (30-min code change). Independent of the proposal; it's a known Round-3 regression and shipping it cleanly with the docs round is the right move.

**One scope cut**: do NOT auto-generate data expansion (4 new AI-gen categories). That's exactly the kind of question the proposal should put to the team — auto-genning would moot the proposal.

## Revised plan (Round 4)

### 1. Files-importer bug fix

Symptom: tap `+ → 文件 / Files`, pick a file, get an error banner ("provide return error" — likely `NSItemProvider`/security-scoped-resource).

Root cause in `client/AAALionApp/AAALionApp/Views/ChatView.swift` lines 50-63:
- `.failure` branch silently swallowed.
- `try? Data(contentsOf: url)` swallows the underlying error.
- Missing `NSFileCoordinator` for cross-process reads (iCloud-Drive files not yet downloaded fail without it).

Fix:
- Surface the `.failure` error via `viewModel.errorMessage`.
- Replace `try?` with a `do/catch` that sets `errorMessage` on failure.
- Use `NSFileCoordinator.coordinate(readingItemAt:options:.forUploading, error:...)` for the read.

Single file change; verifiable on the iPhone with an iCloud-Drive image.

### 2. README polish — icon top-right + Round 3 status

- Embed icon top-right with inline HTML so GitHub renders it: `<img align="right" width="120" src="client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png"/>`
- Refresh "Live status" table:
  - ✅ Physical iPhone deploy verified
  - ✅ CLIP retrieval (A100)
  - ✅ Voice input (4.2 ⭐)
  - ✅ TTS (4.2 ⭐⭐)
  - ✅ Settings + UserDefaults
  - ✅ Context menu: Edit / Copy / Speak
  - ✅ Camera + (post-fix) Files
  - ✅ New icon + theme
- Update `## Quickstart` with the gear / settings note ("change backend URL at runtime, no rebuild").

### 3. `docs/IMPLEMENTATION_GUIDE.md` — NEW single-page index

Does NOT duplicate other docs. Just indexes them. Sections:

1. **What is 狮选 LionPick** (1 paragraph)
2. **Architecture in 60 seconds** — short ASCII flow; link to `ARCHITECTURE.md`
3. **Subsystem map** — table: subsystem → owner → key file → see-also doc
4. **Build & run** — 5 `aaalion ...` commands (cite `DEPLOY_GUIDE.md`)
5. **3-round timeline** — one paragraph per round, link each to its `docs/commits/` records
6. **Where to look next** (task-oriented):
   - "Change the LLM model" → `services/llm_provider.py` + `POLICY.md`
   - "Expand the catalog" → `DATA.md` + `research/`
   - "Something on iPhone broke" → `TROUBLESHOOTING.md`
   - "Prepare for defense" → `RUBRIC_MAPPING.md` + `demos/`
   - "Onboard a new teammate" → `DEPLOY_GUIDE.md` + `PIPELINE.md`

### 4. `docs/WECHAT_UPDATE_2026-05-24.md` — Chinese paste-ready

Cover Round 3 changes since 5-23 morning:

- ✅ iPhone 13 Pro deploy verified (signed + trusted + installed + tested)
- ✅ New theme (Claude-designed) + new lion app icon (TokenRouter-generated)
- ✅ Settings screen, Edit/Copy/Speak context menu, Camera + Files attachment, mic for voice input, speaker for TTS
- ✅ A100 CUDA working (cu124 torch on driver 580); OpenCLIP indexed 100 product images; image-first retrieval in backend
- ⚠️ Files importer bug — fix shipping with this round
- 📋 New: `docs/PROPOSAL_2026-05-24.md` pushed to remote — **Sam / Tujie please review before any next-round execution**

### 5. `docs/PROPOSAL_2026-05-24.md` — NEW forward proposal (for team review)

Written in a "proposal not command" tone. Sections:

**Status snapshot**
- Link to `RUBRIC_MAPPING.md`, `demos/`, `commits/`.
- One-table summary of what's done.

**What I think we should do next** (priority-ordered):

| Priority | Item | Effort | Suggested owner | Why |
|---|---|---|---|---|
| 1 | Demo video (3-5 min) | 2 hrs | Shufeng + 1 reviewer | PDF strongly encourages a video as backup |
| 2 | Defense slide deck | 4 hrs | Shufeng draft → all review | 6/11 is close |
| 3 | Wire `services/cache.py` | 30 min | Sam | low-risk; 4.4 ⭐ rubric |
| 4 | Real product curation (10-15 entries) | 4 hrs split 3 ways | All | Defense credibility; see `docs/research/` |
| 5 | Golden eval expansion to 30+ cases + recall@5 number | 3 hrs | Tujie | 效果与可靠性 evidence |
| 6 | Stress test (locust or k6) | 2 hrs | Sam | 4.4 ⭐⭐⭐ bonus |
| 7 | Demo video reshoot if anything changes | 1 hr | Shufeng | last week |

**Open decisions** (where I want team input, not unilateral):

- **Data**: stick with 100 AI-gen + manual 10-15 real (my preference), OR expand to 200 with more AI-gen? Justification each way.
- **Cache TTL**: 10 min (my default) vs longer (5-min-fresh + 1-hr-stale)?
- **Demo video format**: scripted scenes (my preference) vs live recording?
- **Owner of defense deck**: I can draft; do Sam / Tujie want sections or a review pass?

**What I'm NOT proposing** (cut-line explicit):
- Shopping cart / ordering (4.1) — would be 1+ week of work; we picked 4.2 + 4.3 as the depth bets.
- Custom Mandarin voice / fine-tuned ASR.
- On-device LLM via CoreML.
- Heavy UI animations beyond current polish.

**How to respond**: inline comment on this file via PR, or message in WeChat, or push edits. **I will NOT start execution until at least one teammate weighs in (or 2026-05-26 passes without response — then I default to the priority list above).**

### 6. Commit record + push

`docs/commits/20260523-010-round4.md` documenting the file-bug fix + docs/proposal changes.

## Critical files

```
client/AAALionApp/AAALionApp/Views/ChatView.swift     # .fileImporter error handling
README.md                                              # icon top-right + status table
docs/IMPLEMENTATION_GUIDE.md                           # NEW — single index
docs/WECHAT_UPDATE_2026-05-24.md                       # NEW — Chinese update
docs/PROPOSAL_2026-05-24.md                            # NEW — proposal for team review
docs/commits/20260523-010-round4.md                    # NEW — commit record
```

## Reuse from existing code/docs

- Icon at `client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png` — already committed, GitHub serves it directly.
- `RUBRIC_MAPPING.md` — basis for proposal's status snapshot.
- `docs/research/README.md` — basis for the proposal's data question.
- `services/cache.py` (already written, not wired) — referenced in proposal item #3.
- `tools/check-secrets.sh` before push.

## Verification

1. **Files importer**: tap `+ → Files`, pick an iCloud-Drive image (preferably not yet downloaded) — the file attaches successfully, OR a clear error message appears in the banner explaining what failed.
2. **README on GitHub**: icon visible top-right; status table shows Round 3 items as ✅.
3. **IMPLEMENTATION_GUIDE.md**: all links resolve when viewed on GitHub.
4. **WECHAT_UPDATE_2026-05-24.md**: reads naturally in Chinese, fits a single WeChat message.
5. **PROPOSAL_2026-05-24.md**: visible on `main` branch of remote; Sam / Tujie can react.
6. `tools/check-secrets.sh` clean.
7. Commits attributed to `Shufeng Chen <shufeng.c.dev@gmail.com>`.
8. `cuda-fuzzing/` mtime on `uc` unchanged.

## Risks & deferrals

- **Proposal might sit unread**: fallback is the proposal's own priority list, after 2026-05-26.
- **File-bug fix on iCloud-Drive files** can't be fully tested in simulator — user verifies on iPhone.
- **No data work** this round (intentional).

## Open question for execution

- **Should the proposal include a defense slide outline draft?** I lean yes — concrete starts get more team feedback than open-ended prompts. 30 extra min.
- **`IMPLEMENTATION_GUIDE.md` format**: table-heavy (my plan, easier to scan) vs prose. Easy to swap if reviewers prefer prose.

---

# Round 5 Plan — RAG Depth + 4.1 Cart + Grader Self-Assessment (2026-05-24)

## Context

The user wants this round to push the project to **rubric depth** per the PDF: hit every bonus tier we said we'd hit, measure what's currently un-measured, harden the engineering, and finish with a grader-style self-assessment. Presentation work (demo video, slide deck) is **explicitly deferred** for now — focus is on substance.

Two user decisions confirmed via clarifier:

1. **Branch strategy**: all commits this round land on `shufeng`; fast-forward merge to `main` only at the end after the grader self-assessment is written and the diff is reviewed.
2. **4.1 shopping cart**: **full implementation** — cart sheet, line-item state, mock checkout. User wants the full bonus track, not the 1-hour stub.

## What "deep per PDF" actually means (gap analysis)

PDF §4 sub-items vs current state:

| Item | Current | Round 5 target |
|---|---|---|
| 4.1 ⭐ 对话式加购 | ❌ | ✅ intent detection + cart add |
| 4.1 ⭐⭐ 购物车管理 | ❌ | ✅ sheet UI + line items + qty + remove |
| 4.1 ⭐⭐⭐ 下单确认流程 | ❌ | ✅ mock checkout (review → confirm → success) |
| 4.2 ⭐ 语音输入 | ✅ | (already shipped) |
| 4.2 ⭐⭐ TTS | ✅ | (already shipped) |
| 4.2 ⭐⭐⭐ 拍照找货 | ✅ (vision LLM + CLIP) | tighten prompt for confident match commit |
| 4.3 ⭐ 多轮 | ✅ | (already shipped) |
| 4.3 ⭐⭐ 反选 | 🟡 prompt-only | ✅ structured negation extraction → Chroma where-clause |
| 4.3 ⭐⭐⭐ 对比 | ✅ | (already shipped) |
| 4.4 ⭐ 缓存 | 🟡 file ready | ✅ wire into route |
| 4.4 ⭐⭐ 首屏 <1s | 🟡 typing dots | ✅ instrumented + verified |
| 4.4 ⭐⭐⭐ 端侧打磨 | ✅ | (already shipped) |
| 效果与可靠性 — 检索准确率 | 🟡 unmeasured | ✅ recall@5/10 on 30+ case eval |
| 效果与可靠性 — 无幻觉 | ✅ | (already shipped, add auto-check) |
| 工程质量 — 私有化部署 | 🟡 Dockerfile only | ✅ docker-compose with full stack |
| 工程质量 — 压力测试 | ❌ | ✅ locust 100 RPS × 60s |

Plus a major RAG depth win that doesn't map to a single PDF item but reads as engineering quality across the board:

- **Hybrid retrieval** (BM25 + dense via RRF) — better recall, especially on long-tail queries
- **Query rewriting** before retrieval — handles vague / underspecified queries
- **Cross-encoder reranking** (BAAI/bge-reranker-base) on top-20 → top-5

## Phases

### Phase A — RAG depth (4-5 hrs)

**A1. Hybrid retrieval (BM25 + dense)**
- `pip install rank-bm25 jieba`
- New `rag/retrieve/bm25.py`: jieba-tokenize the catalog text chunks, build BM25 corpus, return top-k by BM25 score
- New `rag/retrieve/hybrid.py`: RRF fusion of dense (existing `query.query`) + BM25
- `rag_client.top_k()` uses hybrid by default; pure-dense available via env

**A2. Query rewriting**
- New `rag/retrieve/rewrite.py`: 1 LLM call (claude-haiku-4-5) to produce 1-2 alternative phrasings of vague queries
- Skip when query already contains specifics (price, brand, etc.) — cost-aware
- Combine results from all phrasings via RRF

**A3. Negation as filter**
- New `rag/retrieve/negation.py`: LLM extracts `{"exclude_brands": [], "exclude_categories": [], "exclude_keywords": []}` from the user text
- Pass `exclude_brands` and `exclude_categories` to Chroma `where` clause
- Post-filter on `exclude_keywords` in product titles/descriptions

**A4. Cross-encoder reranker**
- Load `BAAI/bge-reranker-base` lazily in `rag/retrieve/rerank.py` (existing stub)
- After dense+BM25 hybrid → 20 candidates → rerank → top 5

**A5. Eval expansion + measurement**
- Grow `rag/eval/golden.jsonl` from 10 to 30+ cases across all categories
- Each case has `expected_product_ids` (verified by hand against catalog)
- `rag/eval/run.py` reports recall@5 and recall@10 (already does); add MRR
- Run baseline (dense only) vs hybrid vs hybrid+rerank, document deltas

### Phase B — Backend hardening (3-4 hrs)

**B1. Wire `services/cache.py`**
- Hook into `routes/chat.py` chat_stream: capture events as they emit, store on done
- On hit: replay with 15ms per delta so streaming feel is preserved
- Telemetry: hit/miss ratio in logs

**B2. First-token latency budget**
- `time.perf_counter()` at: `t_received`, `t_retrieval_done`, `t_first_llm_delta`, `t_done`
- Structured JSON log per request: `{"path":"/chat/stream","retrieval_ms":..,"first_delta_ms":..,"total_ms":..,"cache":"hit|miss"}`
- Target: median `first_delta_ms < 800` on cache miss, `< 100` on hit

**B3. Retry/backoff for LLM upstream**
- Wrap `OpenAICompatibleProvider.stream_chat` with retry on 429/500/502: 3 attempts, 0.5s/1s/2s backoff, then surface `error` SSE event
- Same for Anthropic provider

**B4. Stress test**
- New `tools/stress_test.py` using locust (or `httpx` async batch if locust feels heavy)
- 100 RPS × 60 sec against `/chat/stream` with 10 representative queries
- Report p50/p95/p99 and successful-stream %

**B5. Connection pooling**
- Reuse a single `httpx.AsyncClient` across requests (TokenRouter calls)
- Single `AsyncOpenAI` / `AsyncAnthropic` instance reused
- Already partially done — verify and document

### Phase C — Visual depth (1-2 hrs)

**C1. Prompt tightening for vision queries**
- `rag/prompts/system.md`: add a paragraph: "If the user uploaded an image AND your visual identification matches one of the catalog items by brand + product type, **commit to that match**. Do NOT hedge with 'this product is not in catalog'."
- Verify by re-running the demo 6 photo upload — should now confidently say "这是 X (¥720)" instead of "无匹配".

**C2. Hybrid text+image retrieval**
- When both text AND image present: get top-5 from CLIP, top-5 from text-RAG, union, rerank with cross-encoder
- Better recall when user combines a photo with a text constraint ("这款，但要便宜的")

### Phase D — Real product data + 2 new categories (4-5 hrs)

**D1. Hand-curate 15 real products** in `data/extra/` (gitignored — license-grey):
- 5 categories × 3 products each: 美妆, 数码, 服饰, 食品, 家居
- Each entry: real title from Tmall/JD, real brand, real price, real image URL, real description, 3 real reviews
- Schema-compatible with seed JSON

**D2. Two new categories with 15 AI-gen entries each**
- `5_母婴健康` — handles "助孕用品", 维生素, 孕妇护肤, 助眠等
- `6_家居家具` — handles 蜡烛, 香薰, 家具
- Generate via `tools/generate_products.py` (write this; uses claude-opus via TokenRouter)
- Catalog grows: 100 → 130 (100 seed + 30 new categories) + 15 real side validation = 145 documented products

**D3. Re-ingest**
- Text: `aaalion ingest` → grows from 992 chunks
- Images: `ssh uc 'python -m rag.ingest.run_image'` → grows from 100 → 130 image vectors
- Rsync `.chroma/` back to Mac

### Phase E — 4.1 Cart + Checkout (full impl, 6-7 hrs)

**iOS-side**:
- `client/AAALionApp/AAALionApp/Models/CartItem.swift` — Codable struct: product_id, title, brand, base_price, quantity
- `client/.../Stores/CartStore.swift` — @Observable, UserDefaults-persisted (JSON-encoded list)
- `client/.../Views/CartSheet.swift` — list of line items with +/- quantity, line totals, grand total, "去结算 / Checkout" button
- `client/.../Views/CheckoutView.swift` — review + mock address + "确认下单 / Confirm" → "已下单 / Ordered" success screen
- `ChatView.swift`: cart icon (with badge count) next to gear in the toolbar; opens CartSheet
- `MessageBubbleView.swift` / `ProductCardView.swift`: tap product card → bottom sheet has "加入购物车" button

**Backend-side**:
- `routes/cart.py`: GET /cart (no-op, since cart is client-state for now), or skip — UserDefaults is fine for demo
- Intent detection in `routes/chat.py`: if last user message matches `加入购物车 | 加购 | 下单 | 帮我下个单`, emit a special `cart_intent` SSE event (`{"type":"cart_intent","action":"add"|"checkout"}`) so iOS can act
- Optionally: include the products being discussed in the cart_intent event so iOS knows what to add

**Tests/verification**:
- Manual: ask "推荐一款洗面奶" → tap "加入购物车" on the product card → cart badge shows "1" → tap cart icon → see line item → tap checkout → see success screen
- Persistence: kill the app, reopen, cart contents survive

### Phase F — Engineering polish (2-3 hrs)

**F1. Pre-commit hook**
- New `tools/git-pre-commit.sh` → runs `tools/check-secrets.sh` and exits 1 on findings
- Document in `docs/POLICY.md` how to install: `ln -sf $(pwd)/tools/git-pre-commit.sh .git/hooks/pre-commit`

**F2. Docker compose for private deployment**
- `server/docker-compose.yml`: already exists; verify it brings up backend + (optionally) Chroma via volumes
- Test: `docker compose up` → curl `/health` works → curl `/chat/stream` works

**F3. Backend: streaming connection cancellation**
- If client disconnects mid-stream, server should stop talking to the LLM (don't burn quota)
- Wire via `request.is_disconnected()` checks every N tokens

### Phase G — Grader self-assessment (2 hrs)

**G1. `docs/QUALITY_REVIEW.md`** — written in grader voice:

```markdown
# 狮选 LionPick — Quality Self-Assessment (2026-05-24)

> An objective, grader-style review by the implementer. No marketing fluff.
> Scored against the PDF §7.1 rubric. Each item has: target weight, achieved
> score (0-100), evidence link, gaps, and what would push the score higher.

## Total estimated score

| Dimension | Weight | Score | Weighted |
|---|---|---|---|
| 基础功能完整性 | 35% | 95 | 33.25 |
| 工程质量 | 25% | 88 | 22.00 |
| 效果与可靠性 | 20% | 82 | 16.40 |
| 加分项 | 20% | 78 | 15.60 |
| **Total** | **100%** | — | **87.25** |

(Numbers updated after Round 5 execution.)

## Per-item assessment ...
```

Format: each rubric item gets a row with concrete evidence and an honest gap statement.

**G2. Update `RUBRIC_MAPPING.md` with measured numbers**
- recall@5, recall@10, MRR on golden eval (from Phase A5)
- p50 / p95 / p99 latency (from Phase B4)
- Cache hit ratio (from Phase B2)

**G3. Update `IMPLEMENTATION_GUIDE.md` with Round 5 timeline paragraph**

### Phase H — Branch + merge + commit records (1 hr)

- All Round 5 commits land on `shufeng` (in conventional format, with commit records under `docs/commits/`)
- After grader self-assessment is committed, fast-forward merge `shufeng → main` ONLY after user confirms
- Push both branches; rsync to A100

## Policy additions (proposed; need approval to commit)

Add to `docs/POLICY.md` under "Branch model":

```markdown
### From 2026-05-24 onwards

- All Shufeng's commits land on `shufeng` first.
- Fast-forward merge `shufeng → main` only at the end of each iteration,
  after the grader self-assessment is written and the diff is reviewed.
- `main` remains the always-deployable head; `shufeng` is in-flight work.
```

If you don't want this rule, say so before execution and I'll commit only to `shufeng` without touching `main` at all.

## Critical files (representative)

```
# Phase A (RAG depth)
rag/retrieve/bm25.py                   # NEW — jieba + rank-bm25
rag/retrieve/hybrid.py                 # NEW — RRF fusion
rag/retrieve/rewrite.py                # NEW — LLM query expansion
rag/retrieve/negation.py               # NEW — LLM negation extraction
rag/retrieve/rerank.py                 # UPDATE — bge-reranker-base
rag/retrieve/query.py                  # UPDATE — wire all above
rag/eval/golden.jsonl                  # UPDATE — 30+ cases
rag/eval/run.py                        # UPDATE — MRR + per-category recall

# Phase B (backend)
server/app/routes/chat.py              # UPDATE — cache, latency, intent detection
server/app/services/cache.py           # (already written) — wire-up
server/app/services/llm_provider.py    # UPDATE — retry/backoff
tools/stress_test.py                   # NEW

# Phase C (visual)
rag/prompts/system.md                  # UPDATE — vision-match commit rule

# Phase D (data)
data/extra/<5 cats>/data/*.json        # NEW — 15 hand-curated
data/seed/5_母婴健康/                   # NEW — 15 AI-gen
data/seed/6_家居家具/                   # NEW — 15 AI-gen
tools/generate_products.py             # NEW — claude-opus driver

# Phase E (4.1 cart)
client/.../Models/CartItem.swift            # NEW
client/.../Stores/CartStore.swift           # NEW
client/.../Views/CartSheet.swift            # NEW
client/.../Views/CheckoutView.swift         # NEW
client/.../Views/ChatView.swift             # UPDATE — cart toolbar item
client/.../Views/ProductCardView.swift      # UPDATE — Add-to-cart button or use ProductDetailView
client/.../Views/ProductDetailView.swift    # UPDATE — Add-to-cart button
server/app/routes/chat.py                   # UPDATE — emit cart_intent events

# Phase F (eng polish)
tools/git-pre-commit.sh                # NEW
server/docker-compose.yml              # UPDATE — verify

# Phase G (self-assessment)
docs/QUALITY_REVIEW.md                 # NEW — grader-style report card
docs/RUBRIC_MAPPING.md                 # UPDATE — measured numbers
docs/IMPLEMENTATION_GUIDE.md           # UPDATE — Round 5 paragraph

# Phase H
docs/commits/20260524-011-round5-*.md  # 4-6 record files (one per phase)
docs/POLICY.md                         # UPDATE — branch rule addition
```

## Reuse from existing code/docs

- `rag/store.py` for Chroma; extend with BM25 corpus storage
- `services/cache.py` (already implemented; just wire it)
- `services/llm_provider.py` factory pattern for the LLM retry wrapper
- `tools/aaalion` for all build/test/sync commands
- `tools/check-secrets.sh` before every push
- `xcrun simctl launch -test-query` harness for any new demo capture
- The PDF (`/Users/shufengc/Desktop/rag/课题说明会：...`) as the single source for what "depth" actually means

## Verification (end-to-end)

1. `aaalion eval` reports `recall@5 ≥ 0.80` and `recall@10 ≥ 0.90` on 30-case golden set
2. `tools/stress_test.py` reports `p95 < 3000ms` and `≥ 99%` successful streams at 100 RPS
3. Cache hit ratio ≥ 50% on a repeat-query demo flow
4. iPhone demo (after `aaalion ios-device` rebuild):
   - Type "推荐一款适合油皮的洗面奶不要日系品牌不要含酒精" → negation filter applies → only non-Japanese non-alcohol products surface
   - Upload candle/shampoo photo → CLIP retrieves correct catalog item → LLM commits ("这是 X")
   - Tap product card → "Add to cart" → cart badge increments → tap cart → see line item → checkout → success screen
   - Voice input + TTS still work
   - Settings still work
5. Backend logs show structured JSON per request with all 4 timestamps
6. `docs/QUALITY_REVIEW.md` exists with per-rubric-item scores + evidence + gaps; no item left as "🟡" without explicit explanation
7. `git log --oneline shufeng` shows 4-6 Conventional Commits for Round 5; `main` doesn't move until you approve the merge
8. `tools/check-secrets.sh` clean
9. `cuda-fuzzing/` mtime on uc unchanged

## Risks & deferrals

- **30+ hours of work** in this round. If anything stalls (cross-encoder download, BM25 with Chinese tokenization, cart UI design), I cut the lowest-value items first (priority: skip stress test before skipping cart; skip query rewriting before skipping negation filter).
- **Real data curation** (D1) is the slowest item. If it takes too long, fall back to AI-gen for categories 5+6 and keep `data/extra/` empty with a TODO note.
- **Vision LLM cost**: with cross-encoder reranking, each photo query is now 2-3 API calls (rewrite + rerank + final). Tokenrouter has 1000 reqs; this round will burn ~50. Still safe.
- **Cart persistence** uses UserDefaults — fine for a single-user demo, but won't survive an app reinstall (Settings clears it). Documented in the cart code.
- **The "deep" framing means more surface to test**. After execution I MUST run all 6 demos + 3 new (cart, negation, hybrid retrieval) before merging to main.

## Open question for execution

- **Cart product price**: should checkout sum line totals = quantity × `base_price`, or use the cheapest SKU per product? My default: cheapest SKU (more realistic).
- **Negation extraction failure mode**: if the LLM returns invalid JSON, do I fall back to prompt-only negation (current Round 2 behavior) or refuse the query? My default: silent fallback to prompt-only; log a warning.
- **Cross-encoder model**: `BAAI/bge-reranker-base` (good Chinese, 280MB) vs `BAAI/bge-reranker-v2-m3` (better, 568MB). Default to v1 (smaller, faster, sufficient).

---

# Round 6 Plan (REVISED) — Real-product expansion + funny loading + cart polish + CLAUDE.md (2026-05-24 late night)

## Context

User tested Round 5 on iPhone 13 Pro. Six concrete asks (the previous Round 6 draft was rejected, this is the revision after their feedback):

1. **Slow API responses are boring** — first-delta cache-miss path is 5-10s. NEW ask: funny loading sentence to keep users entertained while waiting.
2. **Catalog too narrow** — "推荐一本书" returns cosmetics. REVISED ask: **do NOT generate fake data**. Instead, search **extensively** for real products across Chinese AND international platforms (Amazon, eBay, etc.). Mark provenance differences (origin country, currency, unit) **clearly in the UI**.
3. **Cannot single-tap-add from chat** — current path is 2 taps via `ProductDetailView`.
4. **Cannot delete cart items** — only swipe (undiscoverable).
5. **External URL** — wants "View on Store" buttons to real product pages.
6. **Plan file consolidation** — wants `CLAUDE.md` at repo root for new-agent bootstrap.

iPhone 13 Pro **is fully connected**, ready for redeploy at end of round.

**User's verbatim constraint on data**: *"for the data, dont generate fake ones, please search extensively for items, you can also include amazon etc, not only just the chinese ones, but you should mark their difference , unit and it should be seen clearly from the user's side. the iphone 13 is fully connected now. I think this can expand our images extensively"*

## My honest take

- **Real data only** is the right call. We've shipped 100+ AI-gen products; adding more would dilute, not strengthen, the defense story. 40-50 hand-curated real products across CN + international platforms is far more credible.
- **Provenance markers** are great UX AND a natural rubric story (multimodal e-commerce → international diversity = engineering depth).
- **Funny loading sentence** is cheap, high delight, low risk. ~1 hour.
- **Don't pivot away from the rest** — items 3-6 ship this round too.
- **Image expansion is the side benefit**: real products = real images = bigger CLIP index = better visual retrieval at no extra LLM cost.

## Plan

### Phase A0 — Funny waiting sentence (1 hr)

**iOS-only feature**. Shown in the assistant placeholder bubble while `isStreaming && !hasFirstDelta`.

- New `client/AAALionApp/AAALionApp/Views/LoadingSentence.swift`: `View` that cycles through phrases every 1.5s with fade-in/out.
- New `client/AAALionApp/AAALionApp/Resources/loading_phrases.json` — 12 funny Chinese lines (committed, easy to edit):
  - "🦁 狮子小哥正在认真比价中…"
  - "📦 翻箱倒柜帮你找好物…"
  - "✨ AI 思考中,请勿按地球的快进键"
  - "🛒 让狮子去逛逛淘宝再回来"
  - "🎯 锁定目标商品,扣动扳机…"
  - "🍵 沏壶茶等等就好"
  - "📚 翻一下产品手册先"
  - "🦴 别催,狮子在啃骨头思考"
  - "🤔 这个问题有点意思…"
  - "🐾 狮爪轻点屏幕,马上呈现"
  - "🔍 放大镜里全是答案"
  - "💫 加载中,想象一下狮子在跳舞"
- Wire into `MessageBubbleView`: when `message.role == .assistant && message.text.isEmpty && isStreaming`, render the typing dots + a `LoadingSentence` below them.
- First-delta arrival hides the sentence (already happens naturally because `text` becomes non-empty).
- **No backend change.** Pure client side.

### Phase A — Real-product data expansion (multi-platform, 8-10 hrs)

**A1. Three Perplexity searches** → committed as `docs/research/2026-05-24-*.md`:
- `2026-05-24-cn-real-products.md`: top-30 real Chinese e-commerce items with **public URLs** from Tmall/JD/Pinduoduo. Focus on categories absent from current catalog: 图书, 户外, 母婴, 家居.
- `2026-05-24-intl-real-products.md`: top-30 real products on Amazon.com / Amazon.co.jp / eBay / Best Buy across all 8 categories. Include both name-brand global items (Apple, Sony, Patagonia) AND niche regional items (MUJI, Pigeon, Tatcha).
- `2026-05-24-cross-border-bestsellers.md`: top-15 cross-border bestsellers (items popular both on Tmall 国际 and Amazon US) — these have natural multi-currency listings that exercise the provenance UI.

**A2. Hand-curate 40 real products** across 8 categories (~5 per category):

| Category | Real-product sources |
|---|---|
| `1_美妆护肤` (refresh) | Tmall (雅诗兰黛), Amazon US (Estée Lauder, Tatcha), Sephora US |
| `2_数码电子` (refresh) | JD.com (Apple CN), Amazon US (Sony WH-1000XM, Apple iPhone), Best Buy |
| `3_服饰运动` (refresh) | Nike CN, Amazon US (Levi's, Patagonia), Uniqlo JP |
| `4_食品生活` (refresh) | Tmall 零食, Amazon US (Trader Joe's, snacks), KitKat Japan |
| `5_母婴健康` (NEW) | Tmall 京东健康, Amazon JP (Pigeon, Combi), iHerb |
| `6_家居家具` (NEW) | IKEA, MUJI 日本, Amazon US (home decor) — includes 蜡烛 (fixes Round 3 candle hallucination) |
| `7_图书音像` (NEW) | 当当, 京东图书, Amazon US (English books), Amazon JP |
| `8_户外运动` (NEW) | Decathlon CN, Amazon US (REI, Coleman), Patagonia |

Each entry hand-verified for: real title, real brand, real URL, real price + currency, real image URL (downloaded locally), real description. Reviews summarized from public reviews (paraphrased, attribution noted).

**A3. Schema extension for provenance**:

Add to every product JSON (`data/seed/<cat>/data/<id>.json`):
```json
{
  ...existing fields...,
  "provenance": {
    "origin_country": "CN" | "US" | "JP" | "DE" | ...,
    "source_platform": "Tmall" | "JD" | "Amazon US" | "Amazon JP" | "eBay" | "AI-gen (demo)",
    "currency": "CNY" | "USD" | "JPY" | "EUR",
    "external_url": "https://...",       // real product page when known; null otherwise
    "shipping_note": "国内现货" | "海外直邮" | "美亚转运" | null
  }
}
```

Existing 100 AI-gen products: keep them with `provenance.source_platform = "AI-gen (demo)"` and `external_url: null`. The UI renders them with a different badge so judges aren't misled.

**A4. Image storage**:
- Download real product images → `data/seed/<cat>/images/<product_id>.jpg` (committed; ~50 × 200KB ≈ 10MB total — well under git's comfortable size).
- License caveat: academic-research demo only, not commercial republish. Source URL stored in each JSON's `provenance.external_url` so attribution is preserved. Note added to `docs/POLICY.md`.

**A5. A100 image embedding refresh**:
- `tools/embed_on_a100.sh`: rsync `data/seed/` to `uc:~/shufeng/AAALion-/data/seed/` → `ssh uc 'cd ~/shufeng/AAALion- && source .venv/bin/activate && python -m rag.ingest.run_image'` → rsync `data/.chroma/` back to Mac.
- Index grows from 100 → ~150 image vectors. Demo: upload a photo of any of the new products → CLIP retrieves correctly.
- Verify `cuda-fuzzing/` mtime unchanged after the sync.

**A6. Re-ingest text + eval**:
- `aaalion ingest` grows chunks from 992 → ~1400.
- `rag/eval/golden.jsonl`: add 10+ new cases covering books / household / sports / cross-border products (e.g. `推荐一本通勤读的小说`, `Sony 降噪耳机`, `母婴维生素`).
- Re-run eval — recall@5 must not regress below 0.70.

### Phase B — Inline cart UX + provenance markers (2-3 hrs)

**B1. Inline `+` pill on `ProductCardView`** (top-right corner of the thumbnail). One-tap add with haptic + brief "已加入" toast. Refactor `MessageBubbleView` NavigationLink → `Button { navigate }` so tap-on-card-body still opens `ProductDetailView` without the SwiftUI quirk.

**B2. Provenance UI on `ProductCardView`**:
- **Flag emoji badge** in the top-left of the thumbnail: 🇨🇳/🇺🇸/🇯🇵/🇪🇺. Drives off `provenance.origin_country`.
- **Currency-aware price**: `¥720` for CNY, `$95.00` for USD, `¥9,800` for JPY. Small `(美元)` / `(日元)` label underneath if non-CNY so users can't confuse the symbols.
- **Brand row**: `Tmall · 雅诗兰黛` or `Amazon US · Apple` or `AI 演示 · 自创品牌`. Drives off `provenance.source_platform`.
- **For AI-gen products**: dim/grayscale the flag + show `演示` text badge so the difference is obvious.

**B3. `ProductDetailView` provenance card**: grouped section showing origin country, platform, currency, shipping note. Followed by the "去原页 / View on Store" button (Phase D).

### Phase C — Cart delete + multi-currency totals (1 hr)

**C1.** Explicit trash button on each `CartSheet` row (keep swipe as fallback).
**C2.** iOS `EditMode` toggle in toolbar — familiar Mail-style affordance.
**C3.** Each cart row shows the same flag + currency as the source product card.
**C4.** Cart grand-total groups by currency: e.g. `小计: ¥1,200 + $35 + ¥9,800`. No FX conversion (would require live rates, out-of-scope). Honest UX given mixed-currency carts.

### Phase D — External URLs (30 min)

**D1.** `external_url` already in schema (Phase A3). Backend `ProductCard` SSE event includes it.
**D2.** iOS `ProductDetailView`: "去原页 / View on Store" button → `Link(url)` opens Safari.
**D3.** `CartSheet` row context-menu → "在商店中查看" entry.
**D4.** Fallback for entries with no real URL (AI-gen): show button as disabled, OR open `https://search.tmall.com/search?q=<URL-encoded title>` so the user still gets somewhere real. **My default**: show as disabled with a small "演示商品" hint — honest is better than fake.

### Phase E — CLAUDE.md consolidation (2 hrs)

The new agent's bootstrap doc — single self-contained file at repo root.

**E1. New `CLAUDE.md`** (~8-12 KB):
- **What is 狮选**: 1 paragraph.
- **Architecture in 60 sec**: ASCII flow diagram (iPhone → SSE → FastAPI → Chroma + CLIP → TokenRouter); link to `docs/ARCHITECTURE.md`.
- **What runs where**: Mac LAN IP, backend port, A100 ssh alias, credentials file path (`~/.config/lionpick/credentials.env`).
- **Subsystem map**: table — subsystem / owner / key file / see-also doc.
- **Current quality**: link to latest `docs/QUALITY_REVIEW.md` numbers.
- **Round-by-round timeline**: one paragraph per round, link each to `docs/commits/`.
- **What's open**: short list of known gaps.
- **Build & run**: 5 `aaalion ...` commands.
- **Common gotchas (top 5)**: file-picker NSFileCoordinator, cert ID vs team ID, backend bind 0.0.0.0, SSE blank-line elision, A100 CUDA wheel.
- **Conventions**: Conventional Commits, branch policy, POLICY.md mechanism.
- **Starting fresh in a new session**: a paste-able prompt that orients a new Claude agent.

**E2. Archive long plan** → `docs/PLAN_ARCHIVE.md` (committed). `~/.claude/plans/...` stays as Claude Code's working file but is no longer canonical.

**E3. `README.md`** "Read these next" section leads with `CLAUDE.md`, then `IMPLEMENTATION_GUIDE.md`.

### Phase F — Commit, push, merge, deploy to iPhone (1.5 hrs)

- Conventional Commits on `shufeng`, one per phase:
  - `feat(ios): funny loading sentence during streaming wait`
  - `feat(data): expand catalog with 40 real products from CN + intl sources`
  - `feat(ios): inline add-to-cart + provenance markers on product cards`
  - `feat(ios): explicit cart delete + multi-currency totals`
  - `feat(ios): view-on-store deep links`
  - `docs(root): consolidate plan into CLAUDE.md for new-agent bootstrap`
- Major-commit record: `docs/commits/20260524-013-round6-real-data-funny-loading.md`.
- After user reviews this round: FF merge `shufeng → main`.
- Rsync to `uc:~/shufeng/AAALion-/`. Verify `cuda-fuzzing/` mtime untouched.
- `aaalion ios-device` → reinstall on iPhone 13 Pro → user verifies: books query works, flag + currency visible, waiting sentence cycles, inline cart works, delete works, deep link opens Safari.

## Critical files

```
# Phase A0 — funny loading
client/AAALionApp/AAALionApp/Views/LoadingSentence.swift      # NEW
client/AAALionApp/AAALionApp/Resources/loading_phrases.json   # NEW
client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift    # UPDATE — show during typing

# Phase A — real data
docs/research/2026-05-24-cn-real-products.md                  # NEW
docs/research/2026-05-24-intl-real-products.md                # NEW
docs/research/2026-05-24-cross-border-bestsellers.md          # NEW
data/seed/{1,2,3,4}_*/data/p_*_real_*.json                    # NEW × 20 (real refresh)
data/seed/{5,6,7,8}_*/                                        # NEW × 4 cats (5 real items each)
data/seed/*/images/*.jpg                                      # NEW (real downloaded images)
tools/embed_on_a100.sh                                        # NEW — rsync + ssh + GPU embed
rag/ingest/chunk.py                                           # UPDATE — index provenance fields
rag/eval/golden.jsonl                                         # UPDATE — +10 cases

# Phase B — inline cart + provenance
client/AAALionApp/AAALionApp/Models/ProductCard.swift         # UPDATE — provenance fields
client/AAALionApp/AAALionApp/Models/CartItem.swift            # UPDATE — provenance fields
client/AAALionApp/AAALionApp/Views/ProductCardView.swift      # UPDATE — inline +, flag, currency, platform
client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift    # NavigationLink → Button refactor
client/AAALionApp/AAALionApp/Views/ProductDetailView.swift    # provenance card + go-to-store button

# Phase C — cart polish
client/AAALionApp/AAALionApp/Views/CartSheet.swift            # trash + EditMode + per-currency totals

# Phase D — URLs
server/app/routes/chat.py                                     # include external_url + provenance in product_card SSE

# Phase E — CLAUDE.md consolidation
CLAUDE.md                                                     # NEW — repo root
docs/PLAN_ARCHIVE.md                                          # NEW — archived 6-round plan
README.md                                                     # UPDATE — link CLAUDE.md first
docs/POLICY.md                                                # UPDATE — note image-license caveat

# Phase F
docs/commits/20260524-013-round6-real-data.md                 # commit record
```

## Reuse from existing code/docs

- `tools/aaalion` for all build/test/sync commands.
- `tools/check-secrets.sh` before every push.
- A100 SSH + Chroma upsert pipeline already in `rag/ingest/run_image.py` (reused via `embed_on_a100.sh`).
- Canonical product JSON schema at `data/seed/1_美妆护肤/data/p_beauty_001.json`.
- `CartStore.add()` already takes any product struct — provenance fields flow through.
- All 24+ existing docs cross-linked from `CLAUDE.md` (not duplicated).
- `MessageBubbleView` typing-dots placeholder already exists — `LoadingSentence` slots in below.
- NO TokenRouter image-gen this round (no fake images).

## Verification

1. **Funny loading**: send any cache-miss query → assistant bubble shows typing dots + a rotating funny sentence underneath → first delta replaces them.
2. **Books query**: `推荐一本适合通勤读的书` → ≥1 real product from `7_图书音像` with a real Amazon / 京东 / 当当 URL.
3. **International product**: `推荐一款无线降噪耳机` → Sony WH-1000XM (or similar real Amazon entry) appears, flag = 🇺🇸 (or 🇨🇳), currency clearly shown.
4. **Provenance visible**: every real-product card shows flag + currency + platform; AI-gen products show `演示` badge clearly.
5. **Mixed-currency cart**: add a CNY product + a USD product → cart shows two-line total ("¥1,200 + $35"), no fake unified sum.
6. **Inline +**: tap "+" pill on chat product card → cart badge increments, no navigation, haptic feedback.
7. **Card body tap**: still opens `ProductDetailView` (regression check).
8. **Cart delete**: tap trash icon → item removed instantly. EditMode toggle works.
9. **External URL**: tap "去原页" on a real-product detail → opens real Tmall / 京东 / Amazon page in Safari.
10. **AI-gen URL**: "去原页" is shown as disabled with "演示商品" hint — no fake redirect.
11. **A100 embedded ≥ 150 images** (verify via Chroma collection count on uc).
12. **`aaalion eval`** recall@5 ≥ 0.70 (no regression).
13. **`CLAUDE.md`** at repo root is self-contained — a fresh `claude` session bootstrapped purely from it lands on its feet.
14. **`tools/check-secrets.sh`** clean.
15. **`main` and `shufeng`** at the same SHA after FF merge.
16. **`cuda-fuzzing/`** mtime on `uc` unchanged.
17. **iPhone 13 Pro reinstall successful** — user confirms all features visually.

## Risks & cuts

- **Real product curation is slow** (~5 min per item × 40 = 3+ hrs of human-style verification). If it drags, cut to 25 items prioritizing the 4 new categories (books, home, maternity, sports) — these are the ones plugging real demo gaps. Refresh of existing 4 categories is nice-to-have.
- **License grey area for images**: downloading product images for academic demo is tolerated but technically copyright. Mitigation: cite source URLs in JSON `provenance.external_url`, keep image set small (<20MB total), don't republish elsewhere, note in `docs/POLICY.md`.
- **Real URLs may rot**: some Amazon SKUs go out of stock. Mitigation: prefer evergreen items (iPhone, Sony WH-1000XM, MUJI staples) over flash deals; accept that final-week URL refresh may be needed.
- **Mixed-currency UX complexity**: easy to over-engineer. Keep it simple — flag + currency symbol + brand-line platform. No FX conversion in v1.
- **iPhone redeploy** depends on cert validity (7-day free-tier). If cert is fresh from Round 5, no re-trust prompt needed; if expired, user needs Settings → VPN & Device Management trust step once.

## Open question for execution

- **Provenance shape**: nested under `provenance` object (my choice, cleaner extension) vs flat top-level fields. Already locked above as nested.
- **Loading sentence: localize for English judges?** My take: Chinese-only — 狮选 is a zh-CN app, lion humor lands in Chinese, English judges can still appreciate the polish.
- **Keep existing 100 AI-gen products or strip them?** My take: keep them with `演示` badge — they're still useful for RAG breadth coverage. The 40 real products are the credibility anchor.
- **CLAUDE.md credential reference**: include literal path `~/.config/lionpick/credentials.env`? My take: yes — that's the point of fresh-agent bootstrap.
