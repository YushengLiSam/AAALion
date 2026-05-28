"""Price-watch HTTP routes (R9.A.4, proposal #7).

Three endpoints:
  * ``POST /price_watch/watch`` — start watching a product at a target.
  * ``GET  /price_watch/alerts`` — get due alerts (current price ≤ target).
  * ``DELETE /price_watch/watch/{product_id}`` — stop watching.

Mirrors the shape of repurchase.py: sync DB ops wrapped in
asyncio.to_thread so the FastAPI event loop stays responsive.

Empty state on /alerts is not an error: a new user with no due
watches returns ``{"alerts": []}`` with 200. iOS hides the banner.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.price_watch_db import (
    DEFAULT_SNOOZE_HOURS,
    compute_due_alerts,
    record_watch,
    remove_watch,
)
from app.services.currency import normalize_product_prices

router = APIRouter(prefix="/price_watch", tags=["price_watch"])

# Same UUID-ish guard as repurchase.py — accept IDFV-style strings.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class WatchRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    product_id: str = Field(min_length=1, max_length=128)
    target_price_cny: float = Field(gt=0)


class WatchResponse(BaseModel):
    id: int
    target_price_cny: float


@router.post("/watch", response_model=WatchResponse)
async def watch_endpoint(req: WatchRequest) -> WatchResponse:
    if not _USER_ID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    try:
        row = await asyncio.to_thread(
            record_watch,
            req.user_id,
            req.product_id,
            target_price_cny=req.target_price_cny,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WatchResponse(id=row["id"], target_price_cny=row["target_price_cny"])


@router.get("/alerts")
async def alerts_endpoint(
    user_id: str = Query(min_length=8, max_length=64),
    limit: int | None = Query(default=None, ge=1, le=20),
    snooze_hours: int = Query(default=DEFAULT_SNOOZE_HOURS, ge=0, le=720),
) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    rows = await asyncio.to_thread(
        compute_due_alerts,
        user_id,
        limit=limit,
        snooze_hours=snooze_hours,
    )
    # Normalize prices on each product so the iOS banner sees the same
    # price_cny / exchange_rate fields as a chat /product_card event.
    if rows:
        products = [r["product"] for r in rows]
        products = await asyncio.to_thread(normalize_product_prices, products)
        for r, p in zip(rows, products):
            r["product"] = p
    return {"alerts": rows}


@router.delete("/watch/{product_id}")
async def remove_watch_endpoint(product_id: str, user_id: str = Query(...)) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    removed = await asyncio.to_thread(remove_watch, user_id, product_id)
    return {"removed": removed}
