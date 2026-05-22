# Honest Answers — 2026-05-22

A scratch page for "be honest" questions Shufeng has asked. Each entry: the question, the honest answer, the confidence, and what I'd do next.

## 1. Will OpenCLIP be useful?

**Confidence: high (assuming you mean OpenCLIP).** Yes — it's the right tool for the **拍照找货** bonus track (4.2). For text retrieval, use a Chinese sentence-embedding model instead (we already use `BAAI/bge-small-zh-v1.5`). Plan: index product images with OpenCLIP on the A100 → store in Chroma `products_image` → query by user-uploaded photo. See [IOS_SETUP.md](IOS_SETUP.md) for the "openclaw" full answer.

## 2. Is the Doubao key from the PDF usable?

**Confidence: high.** No. Confirmed dead. The organizer (Shida Wang) announced in WeChat (screenshot dated 2026-05-22) that a teammate-from-another-team leaked the key on a public GitHub commit, non-participants abused it, and the organizer deactivated it. New keys to be re-distributed.

Don't bother trying variations. Use the backend's auto-fallback (`LLM_PROVIDER` env var; defaults to `echo` if no key is set; `anthropic` if your shell has `ANTHROPIC_API_KEY`).

## 3. Will the iOS app build right now?

**Confidence: high.** No. Full Xcode is **not installed** on this Mac — only Command Line Tools. The Swift sources parse cleanly (verified with `swift -frontend -parse`), but you need Xcode.app from the Mac App Store to build, run a simulator, or deploy to your iPhone 13 Pro. See [IOS_SETUP.md](IOS_SETUP.md) for the install steps.

## 4. Is "Claude Code Mobile" a thing I can set up?

**Confidence: medium-high.** No Anthropic-published product by that name exists as of my knowledge. The Claude iOS app is a chat client, not a coding tool. The realistic substitute is SSH from your iPhone (Blink Shell or Termius) into your MacBook running Claude Code. Details in [IOS_SETUP.md](IOS_SETUP.md).

## 5. Should I use Qdrant or Chroma?

**Confidence: medium.** For solo-dev, **Chroma** — that's what's actually wired now. One process, persistent on disk under `data/.chroma/`, no Docker. Qdrant has nicer filter ergonomics but adds a moving part you'd have to remember to start before every dev session. Switch later if you actually hit Chroma limits (which is unlikely at 100-200 products).

## 6. Should I use Doubao embeddings or sentence-transformers?

**Confidence: medium-high.** While Doubao is down, use **sentence-transformers (`BAAI/bge-small-zh-v1.5`)** — free, offline, ~30 MB model, runs on CPU in milliseconds. When Doubao embeddings come back, you can A/B them — but bge-small-zh is competitive on Chinese retrieval benchmarks. Don't blindly assume Doubao's embedding is better.

## 7. Is the seed dataset safe for the demo?

**Confidence: high.** No. AI-generated, confirmed by recruiters. Use for pipeline development and smoke tests. For the actual demo and eval, source real data — manual curation of 50 entries is the recommended floor.

## 8. Should we wait for teammates?

**Confidence: high.** No. Plan as solo. If teammates contribute, that's bonus. The wechat broadcast went out; if they pick up by Sunday, great. If not, [SOLO_DEV_PLAN.md](SOLO_DEV_PLAN.md) gets us across the line alone.

## 9. Is `make ios` from `~` user error or a tool bug?

**Confidence: high.** Both. User behavior is fine (people don't want to remember `cd`), but `make` is path-relative. Fixed via `tools/aaalion` global helper. Now: `aaalion ios` works from anywhere. Install once with `make install-cli`.

---

## Things I genuinely don't know

- Whether the organizer will re-issue Doubao keys before 2026-06-01.
- Whether your iPhone 13 Pro has cellular data or Wi-Fi-only for the demo room.
- Whether the judges will let you bring your MacBook to the defense or require deployment on a clean machine.
- Whether `LLM_PROVIDER=anthropic` will pass the "no-cheating" sniff test from judges (probably yes — the PDF says "model performance unrestricted" — but worth confirming).
- Whether Sam and Tujie even saw the WeChat message (you said no reply).
