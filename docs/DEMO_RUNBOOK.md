# 狮选 LionPick — Demo / Defense Runbook

> Goal: a **zero-surprise live demo** during the defense window (2026-06-11 →
> 06-19). Read this top-to-bottom 30 min before any demo. Owner of the live
> backend on the day: **Sam (Yusheng)**; app on device: **Shufeng**.

---

## 0. The one risk that kills the demo — backend reachability

The app talks to the backend over a Cloudflare tunnel whose URL is **baked into
`client/.../Config.swift`** (`defaultBackendURL`). Today that is a **quick
tunnel** (`*.trycloudflare.com`) whose hostname **changes every time
`cloudflared` restarts**. If it rotates mid-defense, every app install can no
longer reach the backend.

### Fix A (recommended, ~30 min, do before freeze) — stable named tunnel
Requires a Cloudflare account + a domain (even a cheap one). On the VM:

```bash
cloudflared tunnel login                      # auth once
cloudflared tunnel create lionpick            # creates a stable tunnel UUID
cloudflared tunnel route dns lionpick api.lionpick.<your-domain>
# ~/.cloudflared/config.yml:
#   tunnel: <UUID>
#   credentials-file: /home/<user>/.cloudflared/<UUID>.json
#   ingress:
#     - hostname: api.lionpick.<your-domain>
#       service: http://localhost:8000
#     - service: http_status:404
sudo systemctl restart lionpick-tunnel        # point the existing unit at the named tunnel
```
Then bake `https://api.lionpick.<your-domain>` into `Config.swift`, rebuild the
`.ipa` once, install. The URL is now **permanent** — no more rotation risk.

### Fix B (no domain) — pragmatic mitigation
1. **Do NOT restart `cloudflared` / the VM during the defense window.** A quick
   tunnel keeps its URL as long as the process lives.
2. The app has a **runtime URL override**: long-press the gear icon 1.5 s →
   dev mode → paste the new URL → Save (no rebuild). Know this path cold.
3. If the URL rotates, re-bake `Config.swift` + trigger the **iOS Unsigned IPA**
   GitHub Action and re-sideload (~10 min).

---

## 1. T-30 min pre-flight checklist

```bash
# 1. Backend up + warmed?
curl -s https://<backend>/ready        # expect {"status":"ready", ... "prewarm":"completed"}

# 2. Bump the retrieval cache TTL so warmed queries survive the whole session.
#    On the VM, in the systemd unit / .env, then restart lionpick ONCE early:
#      RAG_RETRIEVAL_CACHE_TTL=3600
#      RAG_PREWARM=1

# 3. Pre-warm every scripted query (English first — slowest cold).
python tools/warm-demo.py --base https://<backend>          # on the Mac/VM
#   round 2 first-tokens should all be < 2s  → cache is warm.

# 4. Phone: airplane-mode OFF, on reliable Wi-Fi/cellular; app opens, lion icon,
#    streams a reply to "推荐降噪耳机". Cert not expired (re-`aaalion resign` weekly).
```

If `/ready` is not `ready`, wait — `/chat/stream` returns 503 while warming
(the app now auto-retries 503 a few times, but don't start the demo cold).

---

## 2. Demo script (≈4 min) — lead with the differentiators

| # | Tap / say | Shows off | Watch for |
|---|---|---|---|
| 1 | "推荐一款适合油皮的洗面奶" | RAG retrieval + **provenance tags** `[目录✓]` (green) vs `[推断?]` (amber) | the green/amber badges — this is the anti-hallucination story |
| 2 | "150 元以内的口红" | hard budget filter (CNY-normalized) | all cards ≤ ¥150 |
| 3 | "推荐降噪耳机,不要苹果" then "也不要 Sony 的" | multi-turn **negation that persists** | Apple gone turn 1, Sony also gone turn 2 (3→2 cards) |
| 4 | upload a product photo | **multimodal** CLIP image search | matches the same product |
| 5 | "Sony WH-1000XM5 和 Bose QC45 哪个好" | comparison → markdown table | side-by-side table |
| 6 | "帮我下单" → confirm sheet | agentic **one-tap order** (R12) | order sheet opens, no contradicting text |
| 7 | Settings → switch **Language → English**, ask "running shoes under 1000" | **in-app i18n** + English replies | UI + assistant both English |
| 8 | product detail → 👍 / 👎, re-ask | **preference learning** re-ranks | ordering shifts |

Backup features if time / questions: group-buy (拼单), price-watch (降价提醒),
repurchase reminders (复购), account login (手机号/密码/Apple).

---

## 3. If something breaks mid-demo

| Symptom | Recovery |
|---|---|
| "Cannot connect to server" | URL rotated → dev-mode Settings → paste current tunnel URL (see §0 Fix B.2) |
| Reply hangs > 40s | cold query; the app auto-retries — wait one beat, or tap a **pre-warmed** query from §2 |
| Backend 503 | still warming; `curl /ready`; app auto-retries 503; give it ~20s |
| English query very slow | it routes to the heavier multilingual reranker; only the FIRST is slow — pre-warm covered it in §1.3 |
| Cert expired (app won't launch) | `aaalion resign` + reinstall (do this the morning of) |

---

## 4. Talking points (for Q&A)

- **Anti-hallucination**: every factual claim carries `[目录✓]` (from catalog)
  or `[推断?]` (inferred) — the model is *instructed and shown* to never invent
  prices/specs. Contrast with sycophantic "I can't find that" assistants.
- **Hybrid retrieval**: dense (BGE) + BM25 + cross-encoder rerank; eval
  recall@5 ≈ 0.88, MRR ≈ 0.83, negation accuracy 1.000 on the audited set.
- **Latency**: two cache layers (response + retrieval memo). Measured on the
  live CPU VM via `tools/warm-demo.py`: a **cold** English query is ~15-18s to
  first token; **warmed it is ~1s (≈16× faster)**, Chinese ~4-11s → ~1s. That
  is exactly why the §1.3 pre-warm matters — every scripted query lands warm.
- **Multimodal**: CLIP image collection for "photo → same product".
- **Full loop**: cart, one-tap order, group-buy, price-watch, repurchase,
  per-account preferences, and an in-app Chinese/English toggle.
- **Known limits (be honest)**: free-tier Apple signing (7-day), the demo
  backend is a single CPU VM (no GPU), Sign-in-with-Apple needs a paid account.
