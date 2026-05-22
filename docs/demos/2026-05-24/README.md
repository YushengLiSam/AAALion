# Demo Results — 2026-05-24 (Round 5)

Refreshed demos after Round 5: hybrid (BM25+dense) retrieval + cross-encoder reranking, structured negation extraction, cart + checkout flow.

## Screenshots

| File | Scenario | Verdict |
|---|---|---|
| [`00-empty-state-r5.png`](00-empty-state-r5.png) | First-launch with new cart toolbar icon (next to gear) | ✅ |
| [`01-basic-rerank.png`](01-basic-rerank.png) | Basic recommendation; products surface via hybrid+rerank pipeline | ✅ |
| [`02-negation-filter.png`](02-negation-filter.png) | "防晒霜，不要含酒精，不要日系" → French (理肤泉/巴黎欧莱雅) returned, model explicitly cites the exclusion | ✅✅ |
| [`03-comparison.png`](03-comparison.png) | A-vs-B comparison; structured 4-5 dimension breakdown | ✅ |
| [`04-cart-intent.png`](04-cart-intent.png) | Query ending in "加入购物车" — `cart_intent` SSE event fires; model acknowledges and recommends | ✅ |

## What's verified by these

- **新主题持续生效** (Round 3 polish): warm-ivory + amber-gold rendered correctly.
- **购物车 UI**: cart icon (with badge) sits in the top-right toolbar (between content area and gear).
- **新检索 pipeline**: queries hit hybrid+rerank → 5 candidates → vision LLM grounds the reply.
- **结构化反选** (Round 5 new): user says "不要 X" → LLM extracts X as a structured filter → applies in retrieval → reply *acknowledges the exclusion explicitly* (see demo 02's "(但其为日系品牌, 已排除)" line).
- **加购意图触发** (Round 5 new): regex matches "加入购物车" → backend emits `cart_intent` SSE event → iOS auto-adds the assistant's products. (Demo 04 — note the bottom message: "我已为珀莱雅双抗精华加入购物车…".)

## What still needs touch-driven testing

Cart sheet, +/− quantity, checkout flow, address form, success screen — these require tapping the UI. Verify on the physical iPhone 13 Pro (deployed this round). Steps:

1. Open the app.
2. Send a query, tap a product card to open detail.
3. Tap "加入购物车". Toast appears.
4. Tap the cart icon (top-right) → CartSheet shows the line item.
5. Tap +/− to change qty. Total updates live. Swipe-to-delete works.
6. Tap "去结算 / Checkout" → CheckoutView shows review + mock address.
7. Tap "确认下单" → success screen.

## Reproduction

```bash
aaalion backend &
xcrun simctl boot "iPhone 17 Pro"
xcrun simctl install booted /tmp/lionpick-derived/Build/Products/Debug-iphonesimulator/狮选.app
xcrun simctl launch booted com.aaalion.lionpick -test-query "<query>"
sleep 12
xcrun simctl io booted screenshot docs/demos/2026-05-24/NN-name.png
```

For physical iPhone deploy (current build is on it):
- iPhone 13 Pro, UDID `7310469E-E396-5197-9408-FF1AD58D4CF2`, signed via Personal Team `V8KDBHKA3P`, cert valid until ~2026-05-29.
- First-launch: Settings → General → VPN & Device Management → Trust (only needed once per cert).
