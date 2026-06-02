"""Auth routes (R10 / accounts) — Sign in with Apple + 手机号验证码.

All calls go through `get_user_store()` (see services/user_store.py), so
the local SQLite backend and Sam's future cloud backend are
interchangeable behind these same endpoints.

  * POST /auth/apple         {identity_token, display_name?} → user
  * POST /auth/phone/start   {phone}                          → {sent, dev_code?}
  * POST /auth/phone/verify  {phone, code}                    → user
  * GET  /auth/me            ?user_id=                         → user | 404
  * POST /auth/migrate       {from_user_id, to_user_id}        → {migrated}

`user` = {user_id, provider, display_name, token}. `token` is the opaque
session token; for the local demo it equals user_id (the client sends it
back as user_id on subsequent requests). The cloud backend may issue a
real signed token here without any client change.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.user_store import get_user_store

router = APIRouter(prefix="/auth", tags=["auth"])

# Loose E.164-ish phone guard (accepts +<country><number> or a bare CN
# 11-digit mobile). Kept permissive — real validation lives in the SMS
# provider on the cloud side.
_PHONE_RE = re.compile(r"^\+?\d{6,15}$")


class AppleRequest(BaseModel):
    identity_token: str = Field(min_length=8)
    display_name: str | None = None


class WechatRequest(BaseModel):
    display_name: str | None = None


class PhoneStartRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=16)


class PhoneVerifyRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=16)
    code: str = Field(min_length=4, max_length=8)


class PasswordRegisterRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=128)   # email or phone
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = None


class PasswordLoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class MigrateRequest(BaseModel):
    from_user_id: str = Field(min_length=1, max_length=128)
    to_user_id: str = Field(min_length=1, max_length=128)


def _with_token(user: dict) -> dict:
    # Demo: token == user_id. Cloud may return a signed token instead;
    # the client treats it opaquely.
    user = dict(user)
    user["token"] = user.get("user_id")
    return user


@router.post("/apple")
async def apple_endpoint(req: AppleRequest) -> dict:
    store = get_user_store()
    try:
        user = await asyncio.to_thread(store.verify_apple, req.identity_token, req.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/wechat")
async def wechat_endpoint(req: WechatRequest) -> dict:
    """R11 DEMO — mock WeChat login. **Not** real WeChat OAuth, which needs
    企业资质 + 微信开放平台 SDK + review. Returns a stable demo WeChat account;
    production swaps the real SDK in behind this same endpoint (the iOS
    button is labelled 「演示」)."""
    store = get_user_store()
    user = await asyncio.to_thread(store.mock_wechat, req.display_name)
    return _with_token(user)


@router.post("/phone/start")
async def phone_start_endpoint(req: PhoneStartRequest) -> dict:
    if not _PHONE_RE.fullmatch(req.phone):
        raise HTTPException(status_code=400, detail="invalid phone")
    store = get_user_store()
    return await asyncio.to_thread(store.start_phone, req.phone)


@router.post("/phone/verify")
async def phone_verify_endpoint(req: PhoneVerifyRequest) -> dict:
    if not _PHONE_RE.fullmatch(req.phone):
        raise HTTPException(status_code=400, detail="invalid phone")
    store = get_user_store()
    try:
        user = await asyncio.to_thread(store.verify_phone, req.phone, req.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/register")
async def password_register_endpoint(req: PasswordRegisterRequest) -> dict:
    """R10.bugfix — email/phone + password registration (no SMS)."""
    store = get_user_store()
    try:
        user = await asyncio.to_thread(
            store.register_password, req.identifier, req.password, req.display_name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/password/login")
async def password_login_endpoint(req: PasswordLoginRequest) -> dict:
    store = get_user_store()
    try:
        user = await asyncio.to_thread(store.verify_password, req.identifier, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.get("/me")
async def me_endpoint(user_id: str) -> dict:
    store = get_user_store()
    user = await asyncio.to_thread(store.get_user, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.post("/migrate")
async def migrate_endpoint(req: MigrateRequest) -> dict:
    store = get_user_store()
    return await asyncio.to_thread(store.migrate, req.from_user_id, req.to_user_id)
