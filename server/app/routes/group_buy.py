"""Group-buy (拼单) HTTP routes (R9.B / proposal #11).

  * ``POST /groupbuy/create``       — open a group on a product.
  * ``POST /groupbuy/{id}/join``    — a real user joins the group.
  * ``GET  /groupbuy/{id}``         — live state (poll this for progress).
  * ``GET  /groupbuy/active``       — groups the user opened.

This is an explicitly-labelled SIMULATION (member growth derived from
elapsed time; see group_buy_db). Sync DB ops wrapped in
asyncio.to_thread; product prices CNY-normalized to match the rest of
the app.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.group_buy_db import (
    create_group,
    get_group,
    join_group,
    list_active_for_user,
)
from app.services.currency import normalize_product_prices

router = APIRouter(prefix="/groupbuy", tags=["groupbuy"])

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class CreateRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    product_id: str = Field(min_length=1, max_length=128)
    target_size: int = Field(default=3, ge=2, le=10)


class JoinRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)


def _normalize(group: dict) -> dict:
    """CNY-normalize the embedded product so the iOS modal shows the same
    price shape as a chat product card."""
    prod = group.get("product")
    if prod:
        group["product"] = normalize_product_prices([prod])[0]
    return group


@router.post("/create")
async def create_endpoint(req: CreateRequest) -> dict:
    if not _USER_ID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    try:
        group = await asyncio.to_thread(
            create_group, req.user_id, req.product_id, target_size=req.target_size
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await asyncio.to_thread(_normalize, group)


@router.post("/{group_id}/join")
async def join_endpoint(group_id: str, req: JoinRequest) -> dict:
    if not _USER_ID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    try:
        group = await asyncio.to_thread(join_group, group_id, req.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await asyncio.to_thread(_normalize, group)


@router.get("/active")
async def active_endpoint(user_id: str = Query(min_length=8, max_length=64)) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    groups = await asyncio.to_thread(list_active_for_user, user_id)
    groups = [await asyncio.to_thread(_normalize, g) for g in groups]
    return {"groups": groups}


@router.get("/{group_id}")
async def get_endpoint(group_id: str) -> dict:
    try:
        group = await asyncio.to_thread(get_group, group_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await asyncio.to_thread(_normalize, group)
