"""Preference-learning HTTP routes (R9.B / proposal #12).

  * ``POST   /preferences/feedback`` — record a 👍 (+1) or 👎 (−1).
  * ``GET    /preferences``          — list a user's current weights.
  * ``DELETE /preferences``          — wipe a user's preferences ("我变了").

Sync DB calls wrapped in asyncio.to_thread, same as repurchase /
price_watch. user_id is the anonymous iOS identifierForVendor.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.preferences_db import (
    get_weights,
    list_preferences,
    record_feedback,
    reset_preferences,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class FeedbackRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    product_id: str = Field(min_length=1, max_length=128)
    # +1 like, -1 dislike.
    signal: int = Field(ge=-1, le=1)


@router.post("/feedback")
async def feedback_endpoint(req: FeedbackRequest) -> dict:
    if not _USER_ID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    if req.signal == 0:
        raise HTTPException(status_code=400, detail="signal must be +1 or -1")
    try:
        result = await asyncio.to_thread(
            record_feedback, req.user_id, req.product_id, req.signal
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("")
async def get_endpoint(user_id: str = Query(min_length=8, max_length=64)) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    weights = await asyncio.to_thread(get_weights, user_id)
    items = await asyncio.to_thread(list_preferences, user_id)
    return {"weights": weights, "items": items}


@router.delete("")
async def delete_endpoint(user_id: str = Query(min_length=8, max_length=64)) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    removed = await asyncio.to_thread(reset_preferences, user_id)
    return {"removed": removed}
