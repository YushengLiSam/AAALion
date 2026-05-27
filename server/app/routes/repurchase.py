"""Repurchase reminder HTTP routes.

Two endpoints:
  * ``POST /repurchase/purchase``   — record a purchase
  * ``GET  /repurchase/reminders``  — get due reminders (with ?limit=N
                                       for open-screen subset)

Both wrap the sync SQLite calls in ``asyncio.to_thread`` so they don't
block the event loop — same pattern as the chat route's retrieval calls.

Empty state on /reminders is **not** an error: a new user with no due
items returns ``{"reminders": []}`` with 200. Callers (iOS open-screen)
render nothing in that case.

Full design: ``docs/REPURCHASE_PLAN.md``.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.repurchase_db import compute_due_items, record_purchase

router = APIRouter(prefix="/repurchase", tags=["repurchase"])


# UUID-ish guard. iOS identifierForVendor is always a UUID; reject everything
# that doesn't at least look like one to keep the table clean. Accept lower
# or upper hex + dashes (UUID variants), 16-64 chars to allow custom client
# ids during dev/test.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class PurchaseRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    product_id: str = Field(min_length=1, max_length=128)
    purchased_at: int | None = Field(default=None, ge=0)
    cycle_days: int | None = Field(default=None, gt=0, le=3650)


class PurchaseResponse(BaseModel):
    id: int
    next_due_at: int


@router.post("/purchase", response_model=PurchaseResponse)
async def record_repurchase(req: PurchaseRequest) -> PurchaseResponse:
    """Persist a purchase. 400 on unknown product_id or malformed user_id."""
    if not _USER_ID_RE.match(req.user_id):
        raise HTTPException(status_code=400, detail="user_id must be a UUID-ish 8-64 char identifier")
    try:
        result = await asyncio.to_thread(
            record_purchase,
            req.user_id,
            req.product_id,
            purchased_at=req.purchased_at,
            cycle_days=req.cycle_days,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PurchaseResponse(id=result["id"], next_due_at=result["next_due_at"])


@router.get("/reminders")
async def get_reminders(
    user_id: str = Query(..., min_length=8, max_length=64),
    limit: int | None = Query(default=None, ge=1, le=50),
    snooze_hours: int = Query(default=24, ge=0, le=720),
) -> dict:
    """Return the list of currently-due repurchase reminders for a user.

    Open-screen flow: iOS calls with ``?limit=3`` on chat-view appearance.
    Settings / monitoring flow: omit ``limit`` to see all due items.

    Empty list is a normal response shape, not an error.
    """
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id must be a UUID-ish 8-64 char identifier")
    items = await asyncio.to_thread(
        compute_due_items,
        user_id,
        limit=limit,
        snooze_hours=snooze_hours,
    )
    return {"reminders": items}
