"""Auth routes (R10 / accounts) — Sign in with Apple + 手机号验证码.

All calls go through `get_user_store()` (see services/user_store.py), so
the local SQLite backend and Sam's future cloud backend are
interchangeable behind these same endpoints.

  * POST /auth/apple         {identity_token, display_name?} → user
  * POST /auth/phone/start   {phone}                          → {sent, dev_code?}
  * POST /auth/phone/verify  {phone, code}                    → user
  * GET  /auth/me            ?user_id=                         → user | 404
  * POST /auth/migrate       {from_user_id, to_user_id}        → {migrated}
  * GET  /auth/enforcement-stats  (仅本机运维)                 → 越权校验 / 限流计数

P0.0 安全止血(docs/SECURITY.md):
  * DEMO_MODE=0(默认)时本地后端不再返回 dev_code,phone/start 和
    password/reset/start 返回 503 + error="sms_not_configured";
  * /auth/me、/auth/migrate 走 app.security 的越权校验(AUTH_ENFORCE_MODE);
  * 发码 / 登录 / 校验类接口按 IP + 手机号/账号限流(app.services.ratelimit)。

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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import security
from app.security import Caller, get_caller
from app.services import ratelimit
from app.services.jwt_session import issue as issue_jwt
from app.services.user_store import SmsNotConfiguredError, get_user_store

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


def _require_session(authorization: str | None, user_id: str) -> None:
    """Authenticate a request as the owner of `user_id`.

    Verifies a `Authorization: Bearer <jwt>` header (the signed session token
    issued at login) and that its subject matches `user_id`. Raises 401 if the
    token is missing / invalid / expired, 403 if it belongs to a different
    account. NOTE: security depends on LIONPICK_JWT_SECRET being set to a real
    secret on the server — with the in-source default the token is forgeable.
    """
    token = security.bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="missing session token")
    # 走统一入口:AUTH_ENFORCE_MODE=enforce 且密钥仍是公开默认值时一律视为无效。
    payload = security.verify_session_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if payload.get("sub") != user_id:
        raise HTTPException(status_code=403, detail="forbidden")


def _sms_not_configured(e: SmsNotConfiguredError) -> JSONResponse:
    """DEMO_MODE=0 + 本地后端:明确告诉 App 短信没配置(而不是假装发送成功)。
    body 只放字符串值:老版本 iOS 把错误 body 解成 [String: String] 取 detail。"""
    return JSONResponse(status_code=503, content={"detail": str(e), "error": "sms_not_configured"})


def _norm_ident(identifier: str) -> str:
    """限流 key 用的账号标识归一化(邮箱大小写不敏感)。"""
    return identifier.strip().lower()


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
async def phone_start_endpoint(req: PhoneStartRequest, request: Request) -> dict:
    if not _PHONE_RE.fullmatch(req.phone):
        raise HTTPException(status_code=400, detail="invalid phone")
    limited = ratelimit.check([("sms_target", f"phone:{req.phone}"), ("sms_ip", security.client_ip(request))])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        return await asyncio.to_thread(store.start_phone, req.phone)
    except SmsNotConfiguredError as e:
        return _sms_not_configured(e)


@router.post("/phone/verify")
async def phone_verify_endpoint(req: PhoneVerifyRequest, request: Request) -> dict:
    if not _PHONE_RE.fullmatch(req.phone):
        raise HTTPException(status_code=400, detail="invalid phone")
    # 6 位验证码只有 10^6 种:不限流就能在 5 分钟有效期内暴力猜中。
    limited = ratelimit.check([("login_ip", security.client_ip(request)), ("login_account", f"phone:{req.phone}")])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        user = await asyncio.to_thread(store.verify_phone, req.phone, req.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/register")
async def password_register_endpoint(req: PasswordRegisterRequest, request: Request) -> dict:
    """R10.bugfix — email/phone + password registration (no SMS)."""
    # 注册会回答"账号已存在"(可枚举账号),且每次都跑一遍 PBKDF2(单进程下可被刷满
    # CPU / 线程池)。单独一个每 IP 桶,不占登录额度。
    limited = ratelimit.check([("register_ip", security.client_ip(request))])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        user = await asyncio.to_thread(
            store.register_password, req.identifier, req.password, req.display_name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/password/login")
async def password_login_endpoint(req: PasswordLoginRequest, request: Request) -> dict:
    limited = ratelimit.check([("login_ip", security.client_ip(request)), ("login_account", f"pw:{_norm_ident(req.identifier)}")])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        user = await asyncio.to_thread(store.verify_password, req.identifier, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.get("/me")
async def me_endpoint(user_id: str, caller: Caller = Depends(get_caller)) -> dict:
    caller.authorize(user_id, route="auth.me")
    store = get_user_store()
    user = await asyncio.to_thread(store.get_user, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.post("/migrate")
async def migrate_endpoint(req: MigrateRequest, caller: Caller = Depends(get_caller)) -> dict:
    # 目标账号必须是调用方本人(否则能往别人账号里灌数据);来源如果也是账号 id,
    # 同样必须是本人——否则就能把受害者账号的数据"迁移"进攻击者账号。
    caller.authorize(req.to_user_id, route="auth.migrate")
    if security.is_account_id(req.from_user_id):
        caller.authorize(req.from_user_id, route="auth.migrate.from")
    store = get_user_store()
    return await asyncio.to_thread(store.migrate, req.from_user_id, req.to_user_id)


@router.post("/verify")
async def verify_token_endpoint(req: TokenVerifyRequest) -> dict:
    """Validate the signed session JWT returned in `jwt` at login. Demonstrates
    the production-grade verifiable token (the demo's opaque `token` path is
    unchanged). Returns the decoded subject + expiry, or 401 if invalid/expired."""
    payload = security.verify_session_token(req.jwt)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return {"valid": True, "user_id": payload.get("sub"), "exp": payload.get("exp")}


# ---------------------------------------------------------------------------
# R11 — account management: change password / forgot-reset / delete
# ---------------------------------------------------------------------------


@router.post("/password/change")
async def password_change_endpoint(req: PasswordChangeRequest, request: Request) -> dict:
    """R11 — change password (caller must supply the current password)."""
    # 旧密码校验同样可以被暴力猜,按登录桶限流。
    limited = ratelimit.check([("login_ip", security.client_ip(request)), ("login_account", req.user_id)])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        user = await asyncio.to_thread(
            store.change_password, req.user_id, req.old_password, req.new_password
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/password/reset/start")
async def password_reset_start_endpoint(req: PasswordResetStartRequest, request: Request) -> dict:
    """R11 — forgot password: request a reset code. DEMO_MODE=1 returns
    `dev_code` in the response (email/SMS is mocked); DEMO_MODE=0 on the
    local backend → 503 sms_not_configured; cloud sends a real message."""
    limited = ratelimit.check([("sms_target", f"pw:{_norm_ident(req.identifier)}"), ("sms_ip", security.client_ip(request))])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        return await asyncio.to_thread(store.start_password_reset, req.identifier)
    except SmsNotConfiguredError as e:
        return _sms_not_configured(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/password/reset/verify")
async def password_reset_verify_endpoint(req: PasswordResetVerifyRequest, request: Request) -> dict:
    """R11 — forgot password: verify the code + set the new password → user."""
    limited = ratelimit.check([("login_ip", security.client_ip(request)), ("login_account", f"pw:{_norm_ident(req.identifier)}")])
    if limited is not None:
        return limited
    store = get_user_store()
    try:
        user = await asyncio.to_thread(
            store.verify_password_reset, req.identifier, req.code, req.new_password
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_token(user)


@router.post("/delete")
async def delete_account_endpoint(
    req: DeleteAccountRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    """R11 — delete (注销) the current account + purge its per-user data
    (preferences / price-watch / repurchase).

    SECURITY: requires a valid session JWT whose subject == req.user_id, so an
    account can only be deleted by its owner — this closes the anonymous-delete
    hole where any caller could destroy any non-password account (phone / apple
    / wechat) just by guessing its user_id. Password accounts must ALSO supply
    their password (defense in depth). The client sends the JWT issued at login
    as `Authorization: Bearer <jwt>`."""
    _require_session(authorization, req.user_id)
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


# ---------------------------------------------------------------------------
# P0.0 — 越权校验 / 限流的运维计数。只回答"真·本机"请求(本机 curl);
# 经 Cloudflare 隧道进来的请求虽然对端也是 127.0.0.1,但带 CF-Connecting-IP,
# 一律当作外部请求返回 404,不暴露这个接口的存在。
# ---------------------------------------------------------------------------


@router.get("/enforcement-stats")
async def enforcement_stats_endpoint(request: Request) -> dict:
    if not security.is_local_operator_request(request):
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "posture": security.posture(),
        "auth_enforcement": security.stats.snapshot(),
        "rate_limit": ratelimit.limiter.stats(),
    }
