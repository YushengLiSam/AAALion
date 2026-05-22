# Demo Results — 2026-05-23 (Round 3)

Refreshed demos after the Round 3 upgrade: new icon, theme, CLIP-based image retrieval on the A100, settings screen, camera + files importer, voice input, TTS.

## Screenshots

| File | Scenario | Verdict |
|---|---|---|
| [`00-empty-state.png`](00-empty-state.png) | First-launch empty state with new "狮选 LionPick" branding | ✅ Polished welcome screen |
| [`01-basic-themed.png`](01-basic-themed.png) | Basic recommendation, new theme + product cards loading real images | ✅ Images now load (was broken in Round 2) |
| [`06-photo-clip.png`](06-photo-clip.png) | Photo upload → **CLIP visual retrieval** on A100 → catalog match | ✅ CLIP returned correct product (was hallucinating in Round 2) |

## What's new in Round 3 (vs Round 2)

- **App icon** (Claude-designed, TokenRouter-generated): friendly lion mascot with shopping-tag mane on warm ivory.
- **Theme**: amber-gold accent, deep-espresso text, SF Pro Rounded.
- **Real product images**: backend emits relative `/static/...` URLs, iOS resolves against `Config.backendURL` in the Codable init (`Models/ProductCard.swift`).
- **Settings screen**: gear → URL field + "Test connection" probe → save to UserDefaults. No more rebuild when LAN IP changes.
- **Edit last message**: long-press user bubble → Edit → text returns to composer, assistant reply discarded.
- **Camera + Files**: `+` menu now offers Photos / Camera / Files (in addition to PhotosPicker).
- **Voice input**: mic button uses Apple `Speech` framework, streams Chinese transcription into the composer.
- **TTS**: long-press assistant bubble → Speak → `AVSpeechSynthesizer` reads it aloud.
- **CLIP image retrieval (A100 GPU)**: 100 product images embedded via OpenCLIP ViT-B/32 on the A100 (took ~5 sec). Backend uses image-first retrieval when the user uploads a photo, falling back to text retrieval. Fixes the Round 2 "candle → beer" hallucination.

## Maps to PDF rubric

| PDF item | Coverage |
|---|---|
| 基础功能完整性 (35%) | ✅ end-to-end loop with real LLM, RAG, streaming, product cards |
| 工程质量 (25%) | ✅ Conventional Commits, commit records, secret-scanner, multi-provider LLM, settings persistence, troubleshooting guide |
| 效果与可靠性 (20%) | ✅ anti-hallucination, CLIP visual grounding, theme polish |
| 4.2 多模态 — 拍照找货 ⭐⭐⭐ | ✅ vision LLM + CLIP retrieval BOTH wired |
| 4.2 多模态 — 语音输入 ⭐ | ✅ mic button + Speech.framework |
| 4.2 多模态 — TTS ⭐⭐ | ✅ long-press → speaker |
| 4.3 对话深度 — 多轮 ⭐ | ✅ (Round 2 demo 03 + edit-message UX is bonus) |
| 4.3 对话深度 — 反选 ⭐⭐ | ✅ (Round 2 demo 04) |
| 4.3 对话深度 — 对比 ⭐⭐⭐ | ✅ (Round 2 demo 05) |
| 4.4 工程优化 — 缓存 ⭐ | ⏳ `services/cache.py` implemented; integration pending |
| 4.4 工程优化 — 首屏极速 ⭐⭐ | ⏳ streaming + typing-dots placeholder shipped; latency budget not measured |

## Re-recording

```bash
aaalion backend &
xcrun simctl boot "iPhone 17 Pro"
xcrun simctl install booted /tmp/lionpick-derived/.../狮选.app
xcrun simctl launch booted com.aaalion.lionpick \
    -test-query "<query>" \
    [-test-image-url "<url>"]
sleep 12
xcrun simctl io booted screenshot docs/demos/2026-05-23/NN-name.png
```
