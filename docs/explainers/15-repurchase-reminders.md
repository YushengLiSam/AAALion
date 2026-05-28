# 15 — Repurchase reminders: reminding you to restock

## What is this?

A new feature Sam (李雨晟) shipped in Round 8.F: the app remembers what
you've bought, and when those items are likely running out, the next
time you open the app it shows a small banner saying "你买的洗面奶差不多
该补货了吗？" with a one-tap "再来一单" button.

It's the same idea as Amazon Subscribe & Save or Walmart's auto-replenish,
but it's recommendation-only — we never auto-buy anything. The user
always confirms.

## Why does it matter?

Consumables — face wash, shampoo, snacks, daily contact lenses — have
predictable lifetimes. A 120 ml face wash lasts about 6 weeks for a
typical user. Most apps wait for you to remember; we proactively
remind.

For the bonus rubric, this is a new R8.F item that wasn't planned
originally — Sam designed it overnight, complete with onboarding flow,
snooze semantics, and a documented spec
(`docs/REPURCHASE_PLAN.md`, 536 lines).

## How we built it

### The cycle data — how long does X last?

Each product category has a default repurchase cycle (in days):

```python
CATEGORY_DEFAULTS = {
    "洗面奶":  42,  # 6 weeks
    "牙膏":   45,
    "面霜":   60,
    "饮料":   14,
    "护肤精华": 30,
    "蓝牙耳机": 730,  # 2 years — durable goods
    ...
}
```

These are conservative baselines. Real users vary wildly (someone with
oily skin uses a face wash 50% faster than someone with dry skin), but
60% accuracy is better than the 0% we'd have without any guess.

### The persistence layer — SQLite

Sam picked SQLite for storage. It's stdlib in Python (no new
dependency), file-based (no separate DB server), and persists across
backend restarts. Lives at `data/repurchase.db` (gitignored so it
doesn't pollute git).

Schema (simplified):

```
TABLE purchases:
  user_id     TEXT  (Apple's identifierForVendor — a UUID per app install)
  product_id  TEXT  (which product they bought)
  bought_at   TIMESTAMP
  next_due_at TIMESTAMP  (computed from category cycle)

TABLE snoozes:
  user_id     TEXT
  product_id  TEXT
  snoozed_until TIMESTAMP  (so we don't keep pestering after the user said "later")
```

When the user taps "购买" in the cart, the backend records a purchase.
When they later open the app, we query "any items where next_due_at
is in the past and the user hasn't snoozed?".

Code: `server/app/services/repurchase_db.py` (341 lines).

### The endpoints

Two routes (`server/app/routes/repurchase.py`):

- **`POST /repurchase/purchase`** — body `{user_id, product_id}`.
  Records that the user bought this product. Triggered when checkout
  completes.
- **`GET /repurchase/reminders?user_id=…&limit=3`** — returns up to N
  items that are due for repurchase. Empty list if nothing's due.
  Called by the iOS app every time the chat view appears.

Both endpoints wrap the sync SQLite calls in `asyncio.to_thread` so
they don't block the FastAPI event loop. Same pattern as our retrieval
calls (see [`08-cache-and-speed.md`](08-cache-and-speed.md)).

### The iOS side

When the chat view loads (`ChatView.onAppear`), it calls
`RepurchaseService.fetchReminders(...)`. If the response is non-empty,
a `RepurchaseBannerView` renders above the chat stream:

```
┌─────────────────────────────────────────────────────────┐
│ 该补货了？                                              │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ [thumbnail] 珊珂洗颜专科泡沫洁面乳 (120g)            │ │
│ │             上次购买 42 天前 · 估计余量已不足        │ │
│ │             [再来一单]  [推迟提醒]                   │ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

The user can:

- **再来一单** — adds the product to the cart immediately (haptic +
  toast feedback).
- **推迟提醒** — snoozes this reminder for ~48 hours (POST a snooze;
  the backend hides this reminder from `/reminders` until the snooze
  expires).
- **Tap the card** — opens the product detail view to read more
  before deciding.

Code: `client/AAALionApp/AAALionApp/Views/RepurchaseBannerView.swift`
+ `client/AAALionApp/AAALionApp/Services/RepurchaseService.swift`.

### The device-ID strategy

We need a per-user identifier but we don't have user accounts. iOS gives
us `UIDevice.identifierForVendor` — a UUID that's stable for the same
app on the same device but resets if the user uninstalls. Good enough
for "remember what this user bought" without crossing into user
profiling.

Sam called out an important caveat in the design doc: if the user
uninstalls and reinstalls, they get a new ID and lose their purchase
history. That's acceptable for a v1; v2 would tie to an account.

### Tests

`server/tests/test_repurchase.py` (212 lines) has 7 unit tests:

1. Recording a purchase creates a row.
2. `next_due_at` is computed from the category default.
3. A product NOT due is excluded from reminders.
4. A product PAST due IS included.
5. Snoozing hides a reminder.
6. Multiple users don't see each other's items.
7. Duplicate purchases update the timestamp (not insert).

All pass.

## Honest limitations

- **The cycle data is hand-picked.** No machine learning. A user who
  uses face wash twice a day gets the same recommendation as a user
  who uses it weekly. Real personalization would need usage telemetry,
  which we don't collect.
- **The user_id resets on uninstall.** Per Apple's policy, this is
  unavoidable without a login.
- **The banner only shows on chat-view appear.** No push notifications.
  Push would require an Apple Developer Account, signing entitlements,
  and a notification service — more work than we can justify for a
  demo. The banner-on-open pattern is the lightweight alternative.

## Where this comes from in Sam's work

The full design lives in `docs/REPURCHASE_PLAN.md` on the `main`
branch. Sam wrote that doc first (Phase 0-5 spec + onboarding + 5
review improvements he wrote himself for himself), THEN implemented it.
That's exemplary discipline — design first, code second, tests third.

## Where to dig deeper

- `docs/REPURCHASE_PLAN.md` — Sam's full spec (LIVE on `main` only;
  not in this branch yet because we haven't merged main back into
  `shufeng` yet).
- `server/app/services/repurchase_db.py` — the SQLite layer.
- `server/app/routes/repurchase.py` — the HTTP endpoints.
- `server/tests/test_repurchase.py` — the 7-test matrix.
- `client/AAALionApp/AAALionApp/Views/RepurchaseBannerView.swift` —
  the iOS UI.
- [`14-honest-tradeoffs.md`](14-honest-tradeoffs.md) §"No
  personalization" — sibling decision on what we DON'T track.
