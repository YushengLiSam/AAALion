"""Currency-rate route — exposes Tujie's existing `get_exchange_rate`
helper so the iOS Checkout view can compute on-demand conversions when
the user explicitly picks a target currency.

This is the **explicit context** path: the chat / reminders flows still
honor Tujie's R7.2 "language-context drives currency" behavior. But at
checkout time, the user is literally pressing a button that says
"settle in ¥" or "settle in $" — that IS the language context, so we
fetch and apply the rate immediately.

The underlying helper handles:
  * Frankfurter cross-rate fetch (any A → B pair).
  * In-memory TTL cache so repeat calls don't slam the upstream.
  * Stale fallback if Frankfurter is down → returns the last quote
    flagged `stale=true`, never hard-fails when we have something.

Returning 503 only when we have literally no cached quote AND the live
call failed; the iOS client treats that as "show source price, warn
user".
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.services.currency import get_exchange_rate

router = APIRouter(prefix="/currency", tags=["currency"])


@router.get("/rate")
async def fetch_rate(
    source: str = Query(..., min_length=3, max_length=3, description="ISO 4217 source code"),
    target: str = Query(..., min_length=3, max_length=3, description="ISO 4217 target code"),
) -> dict:
    """Return the latest available `source → target` quote.

    Same-currency request short-circuits to rate=1.0. Otherwise we run
    the sync `get_exchange_rate` in a threadpool (it does network I/O
    under the hood); on a hard miss with no cached fallback we 503.
    """
    src = source.upper()
    tgt = target.upper()
    if src == tgt:
        return {
            "source_currency": src,
            "target_currency": tgt,
            "rate": 1.0,
            "rate_date": "",
            "provider": "identity",
            "stale": False,
        }
    quote = await asyncio.to_thread(get_exchange_rate, src, tgt)
    if quote is None:
        raise HTTPException(status_code=503, detail=f"exchange rate {src}->{tgt} unavailable")
    return quote.payload()
