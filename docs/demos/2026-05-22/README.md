# Demo Results — 2026-05-22

Six scripted demo scenarios run end-to-end against the live stack: SwiftUI iOS app → URLSession SSE → FastAPI → Chroma (bge-small-zh-v1.5) → TokenRouter (claude-haiku-4-5). Driven via `xcrun simctl launch booted com.aaalion.lionpick -test-query <q> [-test-image-url <url>]`.

## Results

| # | Scenario | File | Verdict | Notes |
|---|---|---|---|---|
| 01 | Basic recommendation | [`01-basic-recommendation.md`](01-basic-recommendation.md) | ✅ PASS | Structured Chinese reply + 3 product cards |
| 02 | Conditional filter (price) | [`02-conditional-filter.md`](02-conditional-filter.md) | ✅ PASS | Anti-hallucination working — honest "no match" |
| 03 | Multi-turn | [`03-multi-turn.md`](03-multi-turn.md) | 🟡 WEAK | Turn 1 captured; turn 2 verified in API only |
| 04 | Negation / exclusion | [`04-negation.md`](04-negation.md) | ✅ PASS | Excluded Japanese brands + alcohol |
| 05 | Comparison | [`05-comparison.md`](05-comparison.md) | ✅ PASS | 4-dimension structured comparison |
| 06 | Photo upload (vision) | [`06-photo-upload.md`](06-photo-upload.md) | ✅ PASS | Vision LLM identified brand; retrieval found the product |

## How these were captured

```bash
# Backend up with TokenRouter
aaalion backend &

# Sim build, install, launch with query
xcodebuild -project client/AAALionApp/AAALionApp.xcodeproj -scheme AAALionApp \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -derivedDataPath /tmp/lionpick-derived build
xcrun simctl install booted /tmp/lionpick-derived/Build/Products/Debug-iphonesimulator/狮选.app
xcrun simctl launch booted com.aaalion.lionpick \
    -test-query "<query>" \
    [-test-image-url "http://127.0.0.1:8000/static/<path>"]

# Wait ~12 sec for streaming to complete
sleep 12
xcrun simctl io booted screenshot docs/demos/2026-05-22/NN-name.png
```

## Maps to PDF rubric

| PDF item | Coverage | Demo IDs |
|---|---|---|
| 基础功能完整性 (35%) — 单轮模糊推荐 | ✓ | 01 |
| 基础功能完整性 — 条件筛选 | ✓ | 02 |
| 进阶 (4.3 加分) — 多轮追问 | partial | 03 |
| 进阶 (4.3 加分) — 对比决策 | ✓ | 05 |
| 高级 (4.3 加分) — 反选/排除 | ✓ | 04 |
| 高级 (4.2 加分) — 拍照找货 (多模态) | ✓ | 06 |

## What still needs work

- **Demo 03 turn 2**: capture as a screenshot. Either via osascript or by adding a `-test-followup-query` launch arg.
- **Demo 06 prompt tightening**: vision LLM is too cautious about catalog matches. One-line prompt change.
- **Category filter on retrieval**: demo 01 returned non-cleansers in cards 2 + 3 (花西子 mascara, etc.) — add a soft category boost.
- **Real product data**: all 6 demos used the AI-generated seed. See [`../research/`](../research/) for why no public dataset fits + the manual-curation plan.

## Re-running

```bash
# Open a terminal, ensure backend is on :8000
aaalion backend
# In a second terminal, run the 6 commands above. ~3 min total.
```
