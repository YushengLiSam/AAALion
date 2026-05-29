<img align="right" width="140" src="client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png" alt="狮选 LionPick app icon"/>

# 狮选 LionPick

> **基于 RAG 的多模态电商智能导购 AI Agent** · _A RAG-powered multimodal e-commerce shopping agent_
>
> 团队 / Team: **AAALion** · 比赛 / Competition: ByteDance 2026 AI 全栈挑战赛
>
> 代码冻结 / Code freeze: 2026-06-10 · 答辩 / Defense: 2026-06-11 → 2026-06-19

狮选 LionPick 是一款移动端的智能导购 Agent：iOS 原生客户端 + FastAPI 流式后端 + 向量检索 + 多模态大模型。用户可以用文字、语音、相机或图片描述需求，Agent 基于真实商品库进行多轮对话推荐，杜绝幻觉。

LionPick is a native iOS shopping assistant. The FastAPI backend streams responses over SSE, retrieves real products from a vector index (Chroma + `bge-small-zh-v1.5` + OpenCLIP ViT-B/32 on A100), and uses a vision-capable LLM via TokenRouter for grounded generation. Multi-turn dialogue, negation/exclusion, comparison, photo-to-product search, voice input, TTS playback.

<br clear="all"/>

## Live status (2026-05-25, stateful multi-turn retrieval + Docker readiness prewarm)

**Headline: production retrieval recall@5 0.982 / MRR 0.856 / negation accuracy 1.000 on the audited 68-case set.**

Latest measured score: **90.0 / 100** ([`docs/QUALITY_REVIEW.md`](docs/QUALITY_REVIEW.md)).
Latest demos: [`docs/demos/2026-05-25/`](docs/demos/2026-05-25/) (basic / filter / negation / multi-turn / compare / no-match).

### Round-by-round delta

| Round | What landed | recall@5 | MRR |
|---|---|---:|---:|
| R3 (2026-05-23) | Theme + icon + voice/TTS + settings + camera + A100 CLIP | — | — |
| R4 (2026-05-23) | Files importer fix, README polish, IMPLEMENTATION_GUIDE | — | — |
| R5 (2026-05-24 AM) | Hybrid+rerank + cart+checkout + grader self-assessment | 0.711 | 0.695 |
| R6 (2026-05-24 PM) | 45 real products + provenance UI + funny loading + CLAUDE.md | 0.684 | 0.647 |
| R6.5 (2026-05-25 AM) | Tujie: synonyms + contextual + price intent merged | 0.816 (31-case) | 0.705 |
| R7 (2026-05-25 PM) | Sam's eval dashboard merged + brand-origin negation fix + re-recorded demos | 0.746 (59-case, pre-audit) | 0.674 |
| **R7 + golden audit (2026-05-25, Tujie)** | **Correct wrong labels against catalog; regenerate dashboard** | **0.830 (59-case / 49 positive)** | **0.771** |
| R7.2 (2026-05-25, Tujie branch) | Live reference-rate CNY display for foreign products + CNY-aware budgets | 0.830 | 0.778 |
| **R7.3 (2026-05-25, merged main, now)** | **R7.2 + teammate negation/brand-origin audit combined** | **0.880** | **0.828** |
| **R7.4 (2026-05-25, Tujie)** | **Category / brand / RMB-budget filters applied during dense + BM25 retrieval** | **0.981 (64-case)** | **0.846** |
| **R7.5 (2026-05-25, Tujie)** | **Multi-turn constraint state: inherit / replace / cancel budget and brand filters** | **0.982 (68-case)** | **0.856** |
| **R7.6 (2026-05-25, Tujie)** | **Docker model bake + startup retrieval prewarm + `/ready` gate** | **0.982 (68-case)** | **0.856** |

### Capability matrix

| Capability | Status | Owner | Proof |
|---|---|---|---|
| iOS chat UI + streaming SSE | ✅ | Shufeng | [`docs/demos/`](docs/demos/) |
| Real LLM via TokenRouter (claude-haiku-4-5) | ✅ | Shufeng | all demos |
| Hybrid retrieval (dense + BM25 + cross-encoder rerank) | ✅ | Shufeng (R5) | [`rag/retrieve/`](rag/retrieve/) |
| **Curated synonym expansion** | ✅ NEW | **Tujie (R6.5)** | [`rag/retrieve/synonyms.py`](rag/retrieve/synonyms.py) |
| **Multi-turn query + constraint state** (inherit / replace / cancel filters) | ✅ NEW | **Tujie (R7.5)** | [`server/app/services/contextual_query.py`](server/app/services/contextual_query.py) + [`constraint_state.py`](server/app/services/constraint_state.py) |
| **Price intent parsing + sort** ("200元以下", "便宜") | ✅ NEW | **Tujie (R6.5)** | [`server/app/services/price_intent.py`](server/app/services/price_intent.py) |
| **Foreign-price CNY normalization** (latest reference FX + original-price trace) | ✅ NEW | **Tujie (R7.2)** | [`server/app/services/currency.py`](server/app/services/currency.py) |
| **Constraint-aware retrieval** (category / subcategory / brand / RMB budget) | ✅ NEW | **Tujie (R7.4)** | [`rag/retrieve/constraints.py`](rag/retrieve/constraints.py) + [`query.py`](rag/retrieve/query.py) |
| **Docker readiness prewarm** (no first-user model download) | ✅ NEW | **Tujie (R7.6)** | [`Dockerfile.rag`](Dockerfile.rag) + [`retrieval_readiness.py`](server/app/services/retrieval_readiness.py) + [`/ready`](docs/API.md) |
| Negation / exclusion (4.3 ⭐⭐) | ✅ **audited: accuracy 1.000** | Shufeng + Yusheng | [`docs/demos/2026-05-25/03-negation.png`](docs/demos/2026-05-25/03-negation.png) + [`brand_origin.py`](rag/retrieve/brand_origin.py) |
| Multi-product comparison (4.3 ⭐⭐⭐) | ✅ | Shufeng | [`docs/demos/2026-05-24/03-comparison.png`](docs/demos/2026-05-24/03-comparison.png) |
| OpenCLIP image retrieval on A100 (4.2 ⭐⭐⭐) | ✅ | Shufeng (R3) | 100 images indexed |
| Voice input + TTS (4.2 ⭐ + ⭐⭐) | ✅ | Shufeng (R3) | Speech / AVSpeechSynthesizer |
| **4.1 Cart + inline-add + CNY-normalized totals + 去原页** | ✅ | Shufeng + Tujie | [`docs/demos/2026-05-24/04-cart-intent.png`](docs/demos/2026-05-24/04-cart-intent.png) |
| **Funny loading sentence** (5-10s wait UX) | ✅ NEW | Shufeng (R6) | [`client/.../Views/LoadingSentence.swift`](client/AAALionApp/AAALionApp/Views/LoadingSentence.swift) |
| **45 real products + provenance UI** (CN + Amazon US/JP) | ✅ NEW | Shufeng (R6) | [`docs/research/2026-05-24-real-products.md`](docs/research/2026-05-24-real-products.md) |
| **Latency + cache instrumentation** | ✅ | Shufeng (R5) | [`server/app/services/cache.py`](server/app/services/cache.py) |
| **Eval dashboard (68-case audited/regression golden, per-scenario, HTML)** | ✅ refreshed after multi-turn state | Sam + Tujie | [`docs/eval_report.html`](docs/eval_report.html) + [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) |
| Physical iPhone 13 Pro deploy | ✅ | Shufeng | weekly `aaalion resign` |

> **R7.6 measured**: multi-turn retrieval persists or cancels filters correctly; Docker builds cache the text/reranker model weights, and FastAPI completes an end-to-end retrieval warmup before `/ready` succeeds. See [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md).

---

## Team / 团队

| 中文名 | 英文名 | 角色 / Role | 模块 / Module |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | 客户端 / iOS lead · 项目兜底 / project fallback | `client/` |
| 李雨晟 | Yusheng Li | 后端 / Backend | `server/` |
| 管图杰 | Tujie Guan | 检索 / RAG | `rag/` |

> Shufeng is the project lead and fallback owner — see [`docs/SOLO_DEV_PLAN.md`](docs/SOLO_DEV_PLAN.md).

## Tech stack / 技术栈

- **客户端 / Client**: Swift 5.9, SwiftUI, iOS 17+. Speech.framework + AVSpeechSynthesizer + PhotosPicker + UIImagePickerController + .fileImporter.
- **后端 / Backend**: Python 3.12, FastAPI, SSE, Pydantic v2 multimodal content union.
- **汇率 / FX display**: Frankfurter v2 latest reference rates (keyless; cached server-side; original source price retained).
- **向量库 / Vector DB**: Chroma in-process. Two collections: `products_text` (1082 chunks via `BAAI/bge-small-zh-v1.5`) + `products_image` (100 vectors via OpenCLIP ViT-B/32 on A100).
- **LLM**: `claude-haiku-4-5` (vision-capable) via TokenRouter. Swappable to Doubao, OpenAI, Anthropic, or local echo via `LLM_PROVIDER` env.
- **Design tokens**: Claude-designed warm-ivory + amber-gold + deep-espresso palette (see [`client/AAALionApp/design-tokens.json`](client/AAALionApp/design-tokens.json)).

## Quickstart / 快速开始

```bash
# 1. Install aaalion helper (works from anywhere)
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"

# 2. Configure (key from https://www.tokenrouter.com/console/token)
cp .env.example server/.env
$EDITOR server/.env   # set TOKENROUTER_API_KEY

# 3. Backend + Chroma text index
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
aaalion ingest                       # 1082 text chunks; rerun after metadata changes
aaalion backend                      # uvicorn on 0.0.0.0:8000

# 4. iOS simulator
aaalion ios-sim                      # regen .xcodeproj, build, install, launch

# 5. (optional) RAG retrieval quality eval
python -m rag.eval.run               # CLI: 7 metrics × 3 retrieval strategies
python -m rag.eval.report            # HTML dashboard → docs/eval_report.html
```

### Docker deployment on Windows (copy and run)

Run the following PowerShell block from the repository root. It deploys a
fully functional local RAG backend without an API key: retrieval, filters,
currency conversion and product cards are real; only answer generation uses
the deterministic `echo` provider.

```powershell
# Create local config once, then explicitly choose the free smoke-test provider.
if (-not (Test-Path server/.env)) { Copy-Item .env.example server/.env }
(Get-Content server/.env -Raw) -replace '(?m)^LLM_PROVIDER=.*$', 'LLM_PROVIDER=echo' |
  Set-Content server/.env -Encoding UTF8

# Build the model-cached image, persist the Chroma text index, and start services.
docker compose -f server/docker-compose.yml down
docker compose -f server/docker-compose.yml build backend
docker compose -f server/docker-compose.yml run --rm --no-deps backend python -m rag.ingest.run
docker compose -f server/docker-compose.yml up -d

# Wait until model and full retrieval-path prewarm has completed.
do {
  Start-Sleep -Seconds 1
  try { $ready = Invoke-RestMethod http://127.0.0.1:8000/ready } catch { $ready = $null }
} until ($ready.status -eq "ready")
$ready
```

Open `http://127.0.0.1:8000/docs` to test the API. To switch the running
deployment to real TokenRouter-generated answers, paste and run this block;
it prompts for the key without printing it and keeps the key only in the
gitignored `server/.env` file.

```powershell
$secureKey = Read-Host "TOKENROUTER_API_KEY" -AsSecureString
$tokenKey = [System.Net.NetworkCredential]::new("", $secureKey).Password
$envText = Get-Content server/.env -Raw
$envText = $envText -replace '(?m)^LLM_PROVIDER=.*$', 'LLM_PROVIDER=tokenrouter'
if ($envText -match '(?m)^TOKENROUTER_API_KEY=') {
  $envText = $envText -replace '(?m)^TOKENROUTER_API_KEY=.*$', "TOKENROUTER_API_KEY=$tokenKey"
} else {
  $envText += "`r`nTOKENROUTER_API_KEY=$tokenKey`r`n"
}
Set-Content server/.env $envText -Encoding UTF8
Remove-Variable tokenKey, secureKey, envText
docker compose -f server/docker-compose.yml up -d --force-recreate backend
do {
  Start-Sleep -Seconds 1
  try { $ready = Invoke-RestMethod http://127.0.0.1:8000/ready } catch { $ready = $null }
} until ($ready.status -eq "ready")
$ready
```

The first build downloads and stores the embedding and reranker weights in
the Docker image. The ingest command writes the Chroma index into `data/.chroma/`
on the host, so it survives container replacement. Run the ingest command
again after changing product data. The backend accepts chat traffic only after
`/ready` confirms embedding, BM25, reranking and one complete retrieval path
are warmed.

### Backend URL: how each developer points the app at their own Mac

Default in `Config.swift` is `http://localhost:8000` — that works for **everyone's
simulator without any setup** (the simulator shares the Mac's network). For a
physical iPhone over LAN you need your Mac's LAN IP, and there are three ways
to set it (each `Config.swift` resolves in this order):

| Scenario | What to do | Persistence |
|---|---|---|
| **Mac simulator** | Nothing. `localhost` already works. | — |
| **Real iPhone, day-to-day** | Open the app → ⚙ Settings → enter `http://<your-mac-LAN-IP>:8000` → Test Connection → Save | UserDefaults, survives app relaunch |
| **One-off testing** | Xcode → Product → Scheme → Edit Scheme → Run → Environment Variables → add `PUBLIC_BACKEND_URL=http://…:8000` | Only while debugging through Xcode |
| **Rebuild-needed override** | Edit `defaultBackendURL` in `Config.swift` (NOT recommended — your change will collide with teammates') | Compile-time |

Find your Mac's LAN IP: `ipconfig getifaddr en0`. The backend must be running
bound to `0.0.0.0` (default in `aaalion backend`), not `127.0.0.1`.

**Do not** commit a personal LAN IP into `Config.swift` — leave the default as
`localhost`. Each developer / evaluator picks the right path above for their
device.

Foreign-source products retain their original amount (for example `$398.00 USD`) and are displayed/totaled in RMB using the latest available reference rate fetched by the backend. This is a shopping-display conversion, not a payment settlement quote; the card detail exposes the rate date and provider.

Text retrieval now extracts category, subcategory, named-brand and RMB-budget constraints before candidate recall. During multi-turn chat, those constraints form an authoritative conversation state: subsequent turns can inherit, replace or cancel a brand/budget restriction without stale anchor text restoring it. The same filter is used by dense and BM25 retrieval; foreign-priced products pass the first price gate and are tested strictly only after current CNY conversion. Set `RAG_HARD_FILTERS=0` to run an A/B baseline for inferred constraints.

The eval dashboard ([`docs/eval_report.html`](docs/eval_report.html)) breaks retrieval quality down by scenario (basic / filter / negation / multiturn / compare / no-match) and reports recall@5/10, MRR, precision@5, **反选准确率** (negation accuracy), 无匹配正确率, and latency. The current production-path result is **recall@5 0.982 / MRR 0.856 / negation accuracy 1.000** on 68 cases; the nine-case `multiturn` slice reaches **recall@5 1.000 / MRR 0.889**, and the four new `constraint-state` regressions reach **MRR 1.000**. With Docker prewarm outside timed cases, mean retrieval latency is **610 ms**; an earlier unwarmed run was **6156 ms** because it included a one-time model-load outlier. See [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) for methodology and measured details.

For iPhone device deploy, see [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md). For the A100 CLIP image index, see [`docs/IMPLEMENTATION_GUIDE.md`](docs/IMPLEMENTATION_GUIDE.md).

## Project layout / 项目结构

```
client/    iOS 客户端 (SwiftUI, Speech, AVFoundation)  ← 陈澍枫
server/    FastAPI 后端 (SSE, multimodal, cache)        ← 李雨晟
rag/       Ingest / retrieve / prompts / eval / CLIP    ← 管图杰
data/      seed/ (committed) + .chroma/ (gitignored)
docs/      架构、流水线、政策、demos、research、proposals
meetings/  会议记录
tools/     aaalion + screenshot + check-secrets
```

## Read these next / 接下来该读这些

| Document | Purpose |
|---|---|
| ⭐ [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md) | Single-page index — start here if new to the repo |
| 📋 [docs/PROPOSAL_2026-05-24.md](docs/PROPOSAL_2026-05-24.md) | Next-iteration proposal (awaiting team review) |
| [docs/RUBRIC_MAPPING.md](docs/RUBRIC_MAPPING.md) | PDF §4 → code/artifact mapping for defense |
| [docs/DEPLOY_GUIDE.md](docs/DEPLOY_GUIDE.md) | Step-by-step for a teammate's MacBook + iPhone ≥13 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | All gotchas + fixes |
| [docs/demos/](docs/demos/) | All recorded demo screenshots + verdicts |
| [docs/research/](docs/research/) | Data-availability research |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | End-to-end design |
| [docs/PIPELINE.md](docs/PIPELINE.md) | Dev SOP |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 20-day plan |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Devices + A100 SSH rules |
| [docs/POLICY.md](docs/POLICY.md) | Team rules + commit format |
| [docs/IOS_SETUP.md](docs/IOS_SETUP.md) | Xcode, signing, weekly resign cadence |
| [docs/API.md](docs/API.md) | Backend endpoints |
| 📊 [docs/EVAL_RESULTS.md](docs/EVAL_RESULTS.md) | RAG retrieval quality numbers + how to regenerate the HTML dashboard |
| [docs/SOLO_DEV_PLAN.md](docs/SOLO_DEV_PLAN.md) | Fallback execution plan |
| [docs/FUTURE_WORK.md](docs/FUTURE_WORK.md) | Stretch ideas |
| [docs/EXECUTION_SUMMARY.md](docs/EXECUTION_SUMMARY.md) | Initial bootstrap summary |
| [docs/commits/](docs/commits/) | Major-commit records |

## License

MIT — see [LICENSE](LICENSE).
