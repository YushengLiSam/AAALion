# 06 — Showing foreign prices in RMB without lying

## What is this?

Our catalog has products from Chinese platforms (priced in CNY 元) and
foreign platforms — Amazon US (USD), Amazon JP (JPY), etc. A Chinese
shopper looking at "Sony WH-1000XM5: $398.00" probably can't quickly
tell if that's a good deal vs the local equivalent. We convert foreign
prices to CNY for display, while keeping the original price and exchange
rate visible so we're not misleading anyone.

## Why does it matter?

Three things would be wrong without this feature:

1. **No comparability.** A user comparing prices across the catalog needs
   them in the same currency to do quick math.
2. **No budget filtering.** "300元以下的耳机" would only find Chinese
   products — Amazon US headphones would never qualify even if their
   converted price was 200元.
3. **Lying by omission.** If we showed only "¥2699" without saying "this
   was converted from $398 USD on 2026-05-27", the user might think
   that's the price they'll pay. Cross-border shipping, currency
   markup, and taxes mean the actual price they pay is different.

The "honest" part is what makes this a defensible feature in a defense
panel rather than a sleight of hand.

## How we built it

### Where the FX rate comes from

We use a free API called **Frankfurter** (frankfurter.app) — it serves
European Central Bank reference rates. It's free, no signup required,
and the rates are publicly documented as official ECB references (not
some proprietary spread).

Why ECB reference rates and not "real" forex? Because we're showing
INDICATIVE prices, not actual payment quotes. ECB references are stable,
auditable, and well-understood. Anyone can verify what rate we used by
checking ecb.europa.eu.

Code: `server/app/services/currency.py`. The function `fetch_rate(source,
target)` calls Frankfurter and caches the result for 1 hour (no point
hitting the API on every chat).

### What every product carries

Every product JSON has a `provenance` block:

```json
{
  "product_id": "p_2_intl_01",
  "title": "Sony WH-1000XM5 Wireless Noise Canceling Headphones",
  "brand": "Sony",
  "base_price": 398.00,
  "provenance": {
    "origin_country": "JP",
    "source_platform": "Amazon US",
    "currency": "USD",
    "external_url": "https://www.amazon.com/...",
    "shipping_note": "Cross-border, allow 7-14 days"
  }
}
```

The `base_price` stays in the original currency forever. It's the
ground-truth value scraped from the source. We never overwrite it.

### What the backend adds at response time

When the chat route returns this product to the iOS app, the
`normalize_product_prices` function (in `currency.py`) enriches the
payload:

```json
{
  "product_id": "p_2_intl_01",
  ...
  "base_price": 398.00,
  "price_cny": 2698.68,          // ← NEW: USD 398 × 6.78 CNY/USD
  "exchange_rate": {              // ← NEW: receipt of the conversion
    "source_currency": "USD",
    "target_currency": "CNY",
    "rate": 6.7806,
    "rate_date": "2026-05-27",
    "provider": "Frankfurter latest reference rate",
    "stale": false
  }
}
```

The `stale` field flags whether the rate came from a successful fresh
fetch or a cached fallback (in case Frankfurter is unreachable). If
stale, the iOS app shows a small warning icon.

### How the iOS app displays it

The product card UI (`client/.../ProductCardView.swift`) uses this rule:

- If `price_cny` is present: show "¥2,698.68" as the primary price,
  with a small "原价 $398.00 USD · 2026-05-27" line below for
  auditability.
- If `price_cny` is missing (FX unavailable): show the original currency
  with no conversion. Better honest than wrong.
- The country flag emoji (🇯🇵 / 🇺🇸 / 🇫🇷 / 🇩🇪 / 🇬🇧 / 🇨🇳) is rendered from
  `provenance.origin_country`.

### The cart math

Cart totals are the tricky part. If you have 1 USD product + 2 CNY
products, what's the "total"?

The wrong answer: convert everything to CNY and show one number. The
right answer: show **per-currency totals** and let the user pick a
settle currency. Our cart sheet (`CartSheet.swift`) groups line items by
currency, shows subtotals per currency (with flags), and only computes
a combined CNY total when ALL line items have a successful FX quote.

Sam shipped a checkout enhancement in R8 that adds a **currency Picker**
on the checkout screen — the user actively chooses CNY / USD / mixed,
rather than us silently picking. This is in
`client/.../CheckoutView.swift`.

### Budget filtering across currencies

When the user says "300元以下", we use `price_cny` (not `base_price`) for
the filter. So a USD product whose `price_cny` is 250 yuan qualifies. A
USD product without a successful FX quote does NOT qualify (we don't
guess). Code: `rag/retrieve/constraints.py`.

## Where it gets called

Every product card going through `/chat/stream` and `/repurchase/reminders`
gets normalized. The normalization adds latency — about 50 ms per batch —
but it's wrapped in `asyncio.to_thread` so it doesn't block the FastAPI
event loop. See [`08-cache-and-speed.md`](08-cache-and-speed.md) for the
async-offload pattern.

## Where to dig deeper

- `server/app/services/currency.py` — Frankfurter client + cache + the
  `normalize_product_prices` function.
- `client/AAALionApp/AAALionApp/Models/ProductCard.swift` — iOS-side
  data model with `priceCNY` / `exchangeRate` fields.
- `client/AAALionApp/AAALionApp/Views/CartSheet.swift` — multi-currency
  cart rendering.
- `client/AAALionApp/AAALionApp/Views/CheckoutView.swift` — Sam's R8
  currency picker.
- `docs/API.md` — the JSON schema for the enriched product card.
- `docs/POLICY.md` §"Provenance and currency" — the explicit policy that
  this is INDICATIVE display, not a payment quote.
