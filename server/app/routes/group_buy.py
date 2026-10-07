"""Group-buy (拼单) HTTP routes (R9.B / proposal #11).

  * ``POST /groupbuy/create``       — open a group on a product.
  * ``POST /groupbuy/{id}/join``    — a real user joins the group.
  * ``GET  /groupbuy/{id}``         — live state (poll this for progress).
  * ``GET  /groupbuy/active``       — groups the user opened.

This is an explicitly-labelled SIMULATION (member growth derived from
elapsed time; see group_buy_db). Sync DB ops wrapped in
asyncio.to_thread; product prices CNY-normalized to match the rest of
the app.

P0.0:
  * create / join / active 的 user_id 若是账号 id(phone:… 等,可枚举)必须带
    sub 匹配的 JWT,见 app.security.Caller;
  * GET /groupbuy/{id} 不需要身份,过去会把成员的 user_id(手机号账号、匿名设备
    id)原样返回给任何知道拼单号的人——现在一律替换成不可逆的短哈希。
"""

from __future__ import annotations

import asyncio
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.security import Caller, get_caller
from app.services.group_buy_db import (
    create_group,
    get_group,
    join_group,
    list_active_for_user,
)
from app.services.currency import normalize_product_prices

router = APIRouter(prefix="/groupbuy", tags=["groupbuy"])

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_:.@\-]{8,64}$")


class CreateRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)
    product_id: str = Field(min_length=1, max_length=128)
    target_size: int = Field(default=3, ge=2, le=10)


class JoinRequest(BaseModel):
    user_id: str = Field(min_length=8, max_length=64)


def _mask_member_id(user_id: str) -> str:
    """成员 id 脱敏:真实成员的 user_id(手机号账号 / 匿名设备 id)拿到就能冒用,
    不能出现在任何人都能查的拼单详情里。用稳定短哈希,iOS 的 ForEach 仍然唯一。"""
    return "u_" + hashlib.sha256(user_id.encode()).hexdigest()[:10]


def _normalize(group: dict) -> dict:
    """CNY-normalize the embedded product so the iOS modal shows the same
    price shape as a chat product card; mask real members' user_ids."""
    prod = group.get("product")
    if prod:
        group["product"] = normalize_product_prices([prod])[0]
    members = group.get("members")
    if members:
        group["members"] = [
            m if m.get("kind") == "simulated" else {**m, "user_id": _mask_member_id(str(m.get("user_id", "")))}
            for m in members
        ]
    return group


@router.post("/create")
async def create_endpoint(req: CreateRequest, caller: Caller = Depends(get_caller)) -> dict:
    if not _USER_ID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    caller.authorize(req.user_id, route="groupbuy.create")
    try:
        group = await asyncio.to_thread(
            create_group, req.user_id, req.product_id, target_size=req.target_size
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await asyncio.to_thread(_normalize, group)


@router.post("/{group_id}/join")
async def join_endpoint(group_id: str, req: JoinRequest, caller: Caller = Depends(get_caller)) -> dict:
    if not _USER_ID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    caller.authorize(req.user_id, route="groupbuy.join")
    try:
        group = await asyncio.to_thread(join_group, group_id, req.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await asyncio.to_thread(_normalize, group)


@router.get("/active")
async def active_endpoint(
    user_id: str = Query(min_length=8, max_length=64),
    caller: Caller = Depends(get_caller),
) -> dict:
    if not _USER_ID_RE.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="invalid user_id")
    caller.authorize(user_id, route="groupbuy.active")
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
