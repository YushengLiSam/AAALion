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
import hmac
import os
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.services.jwt_session import issue as issue_jwt, verify as verify_jwt
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


class TokenVerifyRequest(BaseModel):
    jwt: str = Field(min_length=8, max_length=2048)


# R11 — account management requests.
class PasswordChangeRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class PasswordResetStartRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=128)


class PasswordResetVerifyRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=128)
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=6, max_length=128)


class DeleteAccountRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    password: str | None = None


class AdminDeleteRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


def _with_token(user: dict) -> dict:
    # Demo: the opaque `token` stays == user_id so the existing client keeps
    # working unchanged. We ALSO issue a real signed, expiring HS256 JWT in
    # `jwt` (verify via POST /auth/verify) — the production-grade session
    # credential, additive and backward-compatible (clients ignore extra keys).
    user = dict(user)
    uid = user.get("user_id")
    user["token"] = uid
    if uid:
        user["jwt"] = issue_jwt(uid)
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


@router.post("/verify")
async def verify_token_endpoint(req: TokenVerifyRequest) -> dict:
    """Validate the signed session JWT returned in `jwt` at login. Demonstrates
    the production-grade verifiable token (the demo's opaque `token` path is
    unchanged). Returns the decoded subject + expiry, or 401 if invalid/expired."""
    payload = verify_jwt(req.jwt)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return {"valid": True, "user_id": payload.get("sub"), "exp": payload.get("exp")}


# ---------------------------------------------------------------------------
# R11 — account management: change password / forgot-reset / delete
# ---------------------------------------------------------------------------


@router.post("/password/change")
async def password_change_endpoint(req: PasswordChangeRequest) -> dict:
    """R11 — change password (caller must supply the current password)."""
    store = get_user_store()
    try:
        user = await asyncio.to_thread(
            store.change_password, req.user_id, req.old_password, req.new_password
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/password/reset/start")
async def password_reset_start_endpoint(req: PasswordResetStartRequest) -> dict:
    """R11 — forgot password: request a reset code. DEMO returns `dev_code`
    in the response (email/SMS is mocked); cloud sends a real message."""
    store = get_user_store()
    try:
        return await asyncio.to_thread(store.start_password_reset, req.identifier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/password/reset/verify")
async def password_reset_verify_endpoint(req: PasswordResetVerifyRequest) -> dict:
    """R11 — forgot password: verify the code + set the new password → user."""
    store = get_user_store()
    try:
        user = await asyncio.to_thread(
            store.verify_password_reset, req.identifier, req.code, req.new_password
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/delete")
async def delete_account_endpoint(req: DeleteAccountRequest) -> dict:
    """R11 — delete (注销) the current account + purge its per-user data
    (preferences / price-watch / repurchase). Password accounts must include
    their password to confirm."""
    store = get_user_store()
    try:
        return await asyncio.to_thread(store.delete_user, req.user_id, req.password, True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# R11 — admin: list / delete accounts. Gated by the LIONPICK_ADMIN_TOKEN env
# var (sent as the X-Admin-Token header); the API is DISABLED unless that env
# var is set, so it never opens up by accident.
# ---------------------------------------------------------------------------


def _check_admin(token: str | None) -> None:
    expected = os.getenv("LIONPICK_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin API disabled (set LIONPICK_ADMIN_TOKEN)")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="forbidden")


@router.get("/admin/users")
async def admin_users_endpoint(
    x_admin_token: str | None = Header(default=None),
    limit: int = 200,
) -> dict:
    """R11 — admin: list all accounts (never returns password hashes).
    Requires `X-Admin-Token` == LIONPICK_ADMIN_TOKEN."""
    _check_admin(x_admin_token)
    store = get_user_store()
    users = await asyncio.to_thread(store.list_users, max(1, min(limit, 1000)))
    return {"users": users, "count": len(users)}


@router.post("/admin/delete")
async def admin_delete_endpoint(
    req: AdminDeleteRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """R11 — admin: delete any account by id (no per-user password needed)."""
    _check_admin(x_admin_token)
    store = get_user_store()
    try:
        return await asyncio.to_thread(store.delete_user, req.user_id, None, False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
