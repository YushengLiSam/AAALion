<!-- Header laid out as a table so the icon and the title/subtitle sit in
     separate cells and never overlap. (A right-floated <img> used to let
     the subtitle's blockquote bar render across the icon on GitHub.) -->
<table border="0">
  <tr>
    <td width="150" valign="middle" align="center">
      <img width="130" src="client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png" alt="狮选 LionPick app icon"/>
    </td>
    <td valign="middle">
      <h1>狮选 LionPick</h1>
      <b>基于 RAG 的多模态电商智能导购 AI Agent</b> · <i>A RAG-powered multimodal e-commerce shopping agent</i>
      <br/><br/>
      团队 / Team: <b>AAALion</b> · 比赛 / Competition: ByteDance 2026 AI 全栈挑战赛
      <br/>
      代码冻结 / Code freeze: 2026-06-10 · 答辩 / Defense: 2026-06-11 → 2026-06-19
    </td>
  </tr>
</table>

狮选 LionPick 是一款移动端的智能导购 Agent：iOS 原生客户端 + FastAPI 流式后端 + 向量检索 + 多模态大模型。用户可以用文字、语音、相机或图片描述需求，Agent 基于真实商品库进行多轮对话推荐，杜绝幻觉。

LionPick is a native iOS shopping assistant. The FastAPI backend — **deployed to a GCP cloud VM with push-to-`main` continuous deploy** — streams responses over SSE, retrieves real products from a vector index (Chroma + `bge-small-zh-v1.5` + OpenCLIP ViT-B/32), and uses a vision-capable LLM via TokenRouter for grounded generation. Multi-turn dialogue, negation/exclusion, comparison, photo-to-product search, proactive clarification, conversational cart, voice input, TTS playback.

> **New to the project, or not an engineer?** Start with the plain-English
> tour at [`docs/explainers/README.md`](docs/explainers/README.md) —
> 15 short topic explainers written for anyone with introductory CS, no
> ML background required.

## Live status (Round 10 — cloud deploy + cart depth + latency/UX polish)

**The backend now runs in the cloud** (GCP VM, `systemd`-managed, public HTTPS via Cloudflare tunnel) with **continuous deploy**: a push to `main` auto-deploys in ~2 min with a `/ready` health-check and automatic rollback (`tools/cloud-autodeploy.sh`). The iOS app points at the cloud by default, so **a demo no longer depends on anyone's Mac being on**.

**Retrieval headline: negation accuracy = 1.000 across 20 negation cases (doubled this round with 6 adversarial multi-turn/conflicting-constraint cases); recall@5 ≈ 0.93–0.96 on the current 82-case golden set** depending on the rerank-latency config. Run `python -m rag.eval.run` for the live number.

**R10 — what landed (all live-verified on the cloud + iPhone 12 Pro Max / iPad Air):**
- **4.1 cart depth (full)** — conversational add, **quantity change** ("把数量改成2" / "第二个改成3个"), **delete** ("删掉第二个"), swipe-to-delete, and checkout (address + summary + mock complete).
- **4.4 latency** — **首屏极速**: product cards stream *before* the LLM text (pure reorder, recall unchanged) → cards in **~0.3s on cache-hit**; **two-layer cache** (response + retrieval memo, 8s→0.3s on repeats) surfaced at `GET /cache/stats`; cached LLM provider connection; env-tunable rerank cost knobs.
- **4.4 端侧打磨** — **骨架屏** shimmer placeholders, **收藏 ❤️** with spring bounce + haptic, **滑动** cart swipe-actions; plus proper **Markdown rendering** of replies (real tables/headings/bold instead of raw syntax).
- **#5 主动反问** — when a request is too vague to recommend ("推荐个礼物" / "随便看看"), the agent **asks a clarifying question** instead of guessing, with **tappable quick-reply chips** above the composer.
- **拍照找货 on the cloud** — OpenCLIP image→image retrieval (145 product-image vectors) now runs on the prod VM, not the A100.

Self-assessed score: **~93–94 / 100**. See [`docs/RUBRIC_MAPPING.md`](docs/RUBRIC_MAPPING.md) for the per-item map and [`docs/DEV_LOG.md`](docs/DEV_LOG.md) for the round narrative.

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
| R8 (2026-05-25 eve, Shufeng) | Cache hit-rate panel, multi-turn negation persistence, brand-origin KR/DE/GB, Cloudflare Tunnel, dev-mode gate, voice idle-stop, multi-attachment (≤10) | 0.880 → 0.982 (carried) | 0.856 |
| **R9.A (2026-05-28, Shufeng)** | **Agentic/trust layer: topic-switch reset · provenance tags · "why recommended" card · voice-to-cart · price-watch · comparison/scene · cross-lang brand alias** | *UX layer — retrieval flat* | — |
| **R10 (2026-06-01, Yusheng)** | **Cloud deploy + CD (autodeploy/rollback) · 4.1 conversational quantity/delete · 4.4 首屏 cards-first + two-layer cache + `/cache/stats` · 端侧打磨 (skeleton/❤️/swipe) · Markdown rendering · #5 主动反问 + chips · CLIP on cloud · image-path fix** | 0.93–0.96 (82-case) | — |

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
| Multi-product comparison (4.3 ⭐⭐⭐) | ✅ | Shufeng | [`docs/demos/2026-05-25/05-compare.png`](docs/demos/2026-05-25/05-compare.png) |
| OpenCLIP image→image retrieval (4.2 ⭐⭐⭐) | ✅ **on cloud VM** | Shufeng + Yusheng | 145 image vectors; `rag/retrieve/query.py:query_image` |
| Voice input + TTS (4.2 ⭐ + ⭐⭐) | ✅ | Shufeng (R3) | Speech / AVSpeechSynthesizer |
| **4.1 Cart — full** (add · conversational **quantity** · **delete** · swipe · checkout) | ✅ **R10** | Shufeng + Yusheng | `_detect_cart_intent` + `CartStore` + `CheckoutView` |
| **4.4 首屏极速 + two-layer cache + `/cache/stats`** | ✅ **R10** | Yusheng | cards-first reorder + `rag_client` retrieval memo |
| **4.4 端侧打磨** (skeleton · ❤️ favorite · swipe · Markdown render) | ✅ **R10** | Yusheng | `SkeletonCardView` · `FavoritesStore` · `MarkdownMessageView` |
| **#5 主动反问** (vague query → clarify + tappable chips) | ✅ **R10** | Yusheng | `_needs_clarification` + `clarify` SSE event |
| **Cloud deploy + CD** (autodeploy + rollback) | ✅ **R10** | Yusheng | `tools/cloud-autodeploy.sh` |
| **Funny loading sentence** (5-10s wait UX) | ✅ NEW | Shufeng (R6) | [`client/.../Views/LoadingSentence.swift`](client/AAALionApp/AAALionApp/Views/LoadingSentence.swift) |
| **45 real products + provenance UI** (CN + Amazon US/JP) | ✅ NEW | Shufeng (R6) | [`docs/research/2026-05-24-real-products.md`](docs/research/2026-05-24-real-products.md) |
| **Latency + cache instrumentation** | ✅ | Shufeng (R5) | [`server/app/services/cache.py`](server/app/services/cache.py) |
| **Eval dashboard (68-case audited/regression golden, per-scenario, HTML)** | ✅ refreshed after multi-turn state | Sam + Tujie | [`docs/eval_report.html`](docs/eval_report.html) + [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) |
| Physical iPhone 13 Pro deploy | ✅ | Shufeng | weekly `aaalion resign` |
| **Multi-turn topic-switch reset** (sub_categories contamination fix) | ✅ NEW | Shufeng (R9.A) | [`server/app/services/rag_client.py`](server/app/services/rag_client.py) Path C + [`test_context_contamination.py`](server/tests/test_context_contamination.py) |
| **Per-claim provenance tags** `[目录✓]` / `[推断?]` + per-message tally | ✅ NEW | Shufeng (R9.A) | [`server/app/routes/chat.py`](server/app/routes/chat.py) prompt + [`MessageBubbleView.swift`](client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift) |
| **"Why this is recommended" card** (dense / BM25 / RRF / rerank scores + source citation) | ✅ NEW | Shufeng (R9.A) | [`ProductDetailView.swift`](client/AAALionApp/AAALionApp/Views/ProductDetailView.swift) |
| **Voice-to-cart** ("加入购物车 / 结算" fires the cart action) | ✅ NEW | Shufeng (R9.A) | [`ChatViewModel.swift`](client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift) |
| **Price-drop watch** ("提醒我降价" → SQLite watch + alert) | ✅ NEW | Shufeng (R9.A) | [`price_watch_db.py`](server/app/services/price_watch_db.py) + [`price_watch.py`](server/app/routes/price_watch.py) |
| **Comparison tables · scene/outfit sets · cross-language brand aliasing** | ✅ NEW | Shufeng (R9.A) | [`chat.py`](server/app/routes/chat.py) + [`brand_origin.py`](rag/retrieve/brand_origin.py) |

> **R7.6 measured**: multi-turn retrieval persists or cancels filters correctly; Docker builds cache the text/reranker model weights, and FastAPI completes an end-to-end retrieval warmup before `/ready` succeeds. See [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md).

---

## Team / 团队

| 中文名 | 英文名 | 角色 / Role | 模块 / Module |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | 客户端 / iOS lead · 项目兜底 / project fallback | `client/` |
| 李雨晟 | Yusheng Li | 后端 / Backend | `server/` |
| 管图杰 | Tujie Guan | 检索 / RAG | `rag/` |

> Shufeng is the project lead and fallback owner.

## Tech stack / 技术栈

- **客户端 / Client**: Swift 5.9, SwiftUI, iOS 17+. Speech.framework + AVSpeechSynthesizer + PhotosPicker + UIImagePickerController + .fileImporter.
- **后端 / Backend**: Python 3.12, FastAPI, SSE, Pydantic v2 multimodal content union.
- **汇率 / FX display**: Frankfurter v2 latest reference rates (keyless; cached server-side; original source price retained).
- **向量库 / Vector DB**: Chroma in-process. Two collections: `products_text` (1082 chunks via `BAAI/bge-small-zh-v1.5`) + `products_image` (145 vectors via OpenCLIP ViT-B/32) — both served from the cloud VM.
- **LLM**: `claude-haiku-4-5` (vision-capable) via TokenRouter. Swappable to Doubao, OpenAI, Anthropic, or local echo via `LLM_PROVIDER` env.
- **部署 / Deploy**: GCP VM + `systemd` (`lionpick`, `lionpick-tunnel`, `lionpick-autodeploy.timer`) + Cloudflare tunnel for public HTTPS. Push to `main` → auto-deploy in ~2 min with `/ready` check + rollback (`tools/cloud-autodeploy.sh`).
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

### Docker backend on Windows

```powershell
Copy-Item .env.example server/.env   # add TOKENROUTER_API_KEY, or set LLM_PROVIDER=echo
docker compose -f server/docker-compose.yml up --build -d
do {
  Start-Sleep -Seconds 1
  try { $ready = Invoke-RestMethod http://127.0.0.1:8000/ready } catch { $ready = $null }
} until ($ready.status -eq "ready")
$ready
```

The first Docker build downloads and embeds the retrieval model weights into
the image. The container does not accept chat traffic until `/ready` confirms
that embedding, BM25, reranking and one complete retrieval path are warmed.

### Backend URL: where the app points

`Config.swift`'s `defaultBackendURL` is the **live cloud tunnel** (Cloudflare),
so a freshly-installed app works on **any network — Wi-Fi or cellular — with no
setup**, and a demo doesn't depend on a Mac being on. `Config.swift` resolves in
this order:

| Scenario | What to do | Persistence |
|---|---|---|
| **Cloud (default)** | Nothing. The baked-in tunnel URL just works on the device/simulator. | Compile-time default |
| **Local backend (dev)** | Open the app → ⚙ Settings → enter `http://localhost:8000` (sim) or `http://<your-mac-LAN-IP>:8000` (device) → Test → Save | UserDefaults, survives relaunch |
| **One-off testing** | Xcode → Edit Scheme → Run → Environment Variables → `PUBLIC_BACKEND_URL=http://…:8000` | Only while debugging through Xcode |

> The Cloudflare **quick-tunnel** URL is stable while the tunnel process runs but
> can change if it restarts; Yusheng re-bakes/re-broadcasts it when it does.
> A named-tunnel (permanent domain) upgrade is the one open ops item. For a
> local backend, run `aaalion backend` (binds `0.0.0.0`, not `127.0.0.1`); find
> your Mac's LAN IP with `ipconfig getifaddr en0`.

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
| ⭐ [docs/README.md](docs/README.md) | **Docs index — start here** |
| [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md) | Single-page implementation walkthrough |
| 📓 [docs/DEV_LOG.md](docs/DEV_LOG.md) | Rolling dev log — newest shipping moments at the top |
| 📋 [docs/ROADMAP.md](docs/ROADMAP.md) | Current forward plan (to code-freeze) |
| [docs/COMPETITIVE_ANALYSIS.md](docs/COMPETITIVE_ANALYSIS.md) | 狮选 vs the market (web-researched) |
| 📊 [docs/QUALITY_REVIEW.md](docs/QUALITY_REVIEW.md) · [docs/EVAL_RESULTS.md](docs/EVAL_RESULTS.md) | Grader-style self-assessment · RAG metrics |
| [docs/RUBRIC_MAPPING.md](docs/RUBRIC_MAPPING.md) | PDF §4 → code/artifact mapping for defense |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/API.md](docs/API.md) · [docs/PIPELINE.md](docs/PIPELINE.md) | Design · endpoints · dev SOP |
| [docs/DEPLOY_GUIDE.md](docs/DEPLOY_GUIDE.md) · [docs/IOS_SETUP.md](docs/IOS_SETUP.md) | Teammate setup · Xcode/signing |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · [docs/HARDWARE.md](docs/HARDWARE.md) · [docs/POLICY.md](docs/POLICY.md) | Gotchas · devices/SSH · team rules |
| [docs/DEFENSE_DECK_PROMPT.md](docs/DEFENSE_DECK_PROMPT.md) · [docs/explainers/](docs/explainers/) | Defense deck prompt · plain-language explainers |
| [docs/demos/](docs/demos/) · [docs/research/](docs/research/) · [docs/commits/](docs/commits/) | Demo screenshots · market research · change records |

## License

MIT — see [LICENSE](LICENSE).
