# 07 — currency normalization (Tujie R7.2)

**Query**: `Sony WH-1000XM5 多少钱`

**Verdict**: ✅ PASS — Sony product card shows the CNY-normalized price + small "$398.00 USD" original-price tag underneath, plus the exchange-rate quote in detail view.

## What Tujie's R7.2 commit does

Backend `server/app/services/currency.py`:
1. Calls Frankfurter v2 latest reference rates (free, no key).
2. 24h server-side cache; falls back to stale quote if API is down (`stale: true` flag).
3. Backend serializes BOTH `base_price` (original) AND `price_cny` (converted) AND an `exchange_rate` quote (rate, date, source/target currency, stale flag) in every product_card SSE.

iOS `ProductCard.swift`:
- `displayedPrice` returns `priceCNY ?? basePrice`.
- `displayedCurrencySymbol` returns `¥` whenever priceCNY exists.
- `originalPriceText` shows `$398.00 USD` in a smaller font under the CNY price.
- `exchangeRateText` shows `1 USD = ¥7.21 · 2026-05-25` in detail view.
- The old `currencyHint` "(美元)" parens only render when priceCNY is null (no double-display).

## Why this matters

Mixed-currency catalogs were the R6 weakness. Showing `$398` for a USD product next to `¥720` for CNY was visually inconsistent and the cart total was honest but awkward (`¥1200 + $35`). After R7.2 the user always sees ¥, with the original-currency price as a transparency footnote rather than the primary display.
