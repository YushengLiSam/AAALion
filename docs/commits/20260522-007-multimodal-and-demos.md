# feat: multimodal end-to-end (iOS PhotosPicker + backend content union) + 6 demos recorded + research ingested

**Date**: 2026-05-22 (very late) — early 2026-05-23
**SHA**: (fill in after commit)
**Author**: Shufeng Chen <shufeng.c.dev@gmail.com>

## Why

Round 2 of the plan closes the bonus-track gaps: image input (`拍照找货`, bonus 4.2) was missing, multi-turn / negation / comparison (bonus 4.3) was implemented but never demonstrated, and the Perplexity research outputs the user ran lived in a local `search/` folder invisible to teammates. This commit:

1. Adds end-to-end multimodal support (iOS sends image as OpenAI-style content blocks; backend forwards to vision LLM unchanged).
2. Records six scripted demos (one per scenario) with screenshots + sidecar verdict docs so teammates can see the live system without setting up their dev env first.
3. Moves the search outputs into `docs/research/` with an index that documents the verdict (no usable real Chinese e-commerce dataset publicly available; manual curation is the right move).
4. Updates the README + writes a new WeChat update mapping PDF rubric to actual demo evidence.

## What changed

### iOS multimodal
- `Models/Message.swift` — added `imageData: Data?`. Sent inline in user message bubble + serialized as base64 in the wire content array.
- `Services/ChatService.swift` — `ChatRequest.Content` is now an enum `.plain(String) | .parts([ContentPart])`; per-message build chooses the shape based on whether `imageData` is set.
- `ViewModels/ChatViewModel.swift` — added `pendingImage`, cleared after send. `send()` allows image-only or text-only payloads. `runScriptedQueryIfAny()` now supports `-test-query` and `-test-image-url` (URL fetch; the launch arg can't read iOS-sandboxed paths).
- `Views/ChatView.swift` — composer now has a `PhotosPicker` button (iOS 17+) and a removable thumbnail preview above the input.
- `Views/MessageBubbleView.swift` — renders attached image inline above the text bubble.

### Backend multimodal
- `schemas/chat.py` — `ChatMessage.content: str | list[ContentPart]` where `ContentPart = TextPart | ImagePart` (Pydantic v2 discriminated union).
- `routes/chat.py` — new helpers `_extract_user_text` and `_has_image`. When an image is present, route uses `last_user.model_dump()["content"]` directly as the user message content (preserving the OpenAI-shape content array) and calls `_stream_with_history`. Text-only path unchanged.
- No new route — single `/chat/stream` handles both shapes.

### Research ingest
- Moved `/Users/shufengc/Desktop/rag/search/` → `docs/research/` with cleaned filenames:
  - `2026-05-22-cn-ecommerce-datasets.md`
  - `2026-05-22-multimodal-ecommerce-benchmarks.md`
  - `2026-05-22-cn-ecommerce-apis.md`
- Wrote `docs/research/README.md` with verdict + cross-link to `docs/DATA.md`.
- `docs/DATA.md` now references `docs/research/` at the top.

### Demos
- `docs/demos/2026-05-22/01..06.png` + matching `.md` sidecars + `README.md` index.
- Sidecars contain: user query, screenshot, verbatim assistant reply, product card IDs, verdict (PASS/WEAK/FAIL), pipeline checklist, and tuning notes.
- Demo 06 (photo) verified end-to-end: user image rendered in chat bubble; vision LLM identified the brand from the image; retrieval surfaced the correct product card.

### README + WeChat
- `README.md` — new live status table mapped to demo files; replaced quickstart with `aaalion` flow.
- `docs/WECHAT_UPDATE_2026-05-23.md` — paste-ready Chinese message comparing PDF rubric vs done, ending with explicit asks for Sam (Doubao key) and Tujie (CLIP indexer).

## Procedure

```
# 1. Move + rename search outputs
mkdir -p docs/research
mv /Users/shufengc/Desktop/rag/search/_Find\ publicly\ available\ datasets\ of\ real\ Chinese.md \
   docs/research/2026-05-22-cn-ecommerce-datasets.md
# (and the two HTML-encoded-name files similarly)

# 2. Wire backend multimodal
# (edits to schemas/chat.py + routes/chat.py)

# 3. Smoke-test from CLI
python3 -c "import base64, json, urllib.request
b64 = base64.b64encode(open('data/seed/.../p_beauty_001_live.jpg','rb').read()).decode()
# POST {messages: [{role: user, content: [{type:text,text:...}, {type:image_url,image_url:{url:...}}]}]}
"
# → vision LLM identified ESTEE LAUDER, retrieval returned p_beauty_001 ✓

# 4. iOS changes + regen + rebuild
aaalion ios
xcodebuild ... build  # → BUILD SUCCEEDED

# 5. Capture 6 demos
for q in <queries>; do
  xcrun simctl terminate booted com.aaalion.lionpick
  xcrun simctl launch booted com.aaalion.lionpick -test-query "$q"
  sleep 12
  xcrun simctl io booted screenshot docs/demos/2026-05-22/NN-name.png
done
# Demo 06 used -test-image-url http://127.0.0.1:8000/static/...

# 6. Write sidecar .md + README index + DEPLOY_GUIDE + WeChat update

# 7. Commit (conventional), push
```

## Outcome / Verification

- ✅ Backend `/chat/stream` works for text-only AND multimodal payloads via the Pydantic union schema.
- ✅ iOS PhotosPicker flow works end-to-end: pick image → preview → send → image visible in user bubble → backend received content array → vision LLM saw image → reply streamed.
- ✅ 6 demo screenshots committed under `docs/demos/2026-05-22/` (verdicts: 5× PASS, 1× WEAK — multi-turn turn-2 capture deferred).
- ✅ Research outputs (3 files, ~21 KB each) ingested into `docs/research/` with index README.
- ✅ README's live status table maps each capability to a demo file or a clear ⏳ next-step.
- ✅ `tools/check-secrets.sh` clean after commit.

## Follow-ups

- User: paste the 10-char Team ID once they're back at the Mac → I'll set `DEVELOPMENT_TEAM` + deploy to the iPhone 13 Pro.
- Tighten demo 06's prompt: vision LLM should commit to a catalog match when it identifies a brand + product type. One-line system-prompt change planned.
- Capture demo 03 turn 2 in the SwiftUI render (either via osascript or by extending launch-arg harness).
- CLIP image indexer on `uc` via CPU torch (no driver risk). Wires up to a `products_image` Chroma collection for true 拍照找货 retrieval.
- Grow `rag/eval/golden.jsonl` from 10 to 30+ cases.
