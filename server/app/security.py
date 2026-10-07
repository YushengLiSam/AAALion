"""P0.0 安全止血的公共部件:越权(IDOR)校验、客户端 IP 识别、开关与统计。

背景(docs/SECURITY.md 有完整的 威胁 → 修复 → 开关 → 验证 表):
preferences / price_watch / repurchase / group_buy 以及 /auth/me、/auth/migrate
过去直接信任客户端传来的 user_id。账号 id 的格式是可枚举的(phone:<手机号> 等),
知道别人手机号就能读写他的偏好、降价提醒、复购记录、拼单。

本模块提供**一个**依赖 `get_caller`,路由里拿到 `Caller` 后调用
`caller.authorize(user_id, route=...)`:
  * 账号 id(见 is_account_id)必须带 `Authorization: Bearer <JWT>` 且 sub == user_id;
  * 匿名设备 id(iOS identifierForVendor / 随机 UUID,不可枚举)照旧放行,不要求 token。

灰度开关 AUTH_ENFORCE_MODE = off | report | enforce(默认 report):
  * off      完全不检查(紧急回滚用);
  * report   照常放行,但打结构化 WARNING 日志并计数(GET /auth/enforcement-stats,仅本机);
  * enforce  违规直接 401(缺/坏 token)或 403(token 属于别人)。

失效保护:LIONPICK_JWT_SECRET 仍是源码里的公开默认值时,JWT 任何人都能伪造。
enforce 模式下此时**所有 token 一律视为无效**(fail closed),否则强制校验只是摆设。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import threading
import time

from fastapi import Header, HTTPException, Request

from app.services import jwt_session

log = logging.getLogger("lionpick.security")

ENFORCE_MODES = ("off", "report", "enforce")
_DEFAULT_ENFORCE_MODE = "report"


# ---------------------------------------------------------------------------
# 开关
# ---------------------------------------------------------------------------


def enforce_mode() -> str:
    """每次请求时读环境变量(便宜;测试可 monkeypatch)。非法值按默认 report 处理。"""
    raw = os.getenv("AUTH_ENFORCE_MODE", _DEFAULT_ENFORCE_MODE).strip().lower()
    return raw if raw in ENFORCE_MODES else _DEFAULT_ENFORCE_MODE


def demo_mode_enabled() -> bool:
    from app.services.user_store import demo_mode_enabled as _dm

    return _dm()


def jwt_secret_is_default() -> bool:
    return jwt_session.using_default_secret()


def tokens_trustworthy() -> bool:
    """enforce + 公开默认密钥 → 不信任任何 token。"""
    return not (enforce_mode() == "enforce" and jwt_secret_is_default())


def verify_session_token(token: str) -> dict | None:
    """统一的 JWT 校验入口(auth._require_session 和 Caller 都走这里)。"""
    if not tokens_trustworthy():
        return None
    return jwt_session.verify(token)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    tok = authorization.split(" ", 1)[1].strip()
    return tok or None


# ---------------------------------------------------------------------------
# 账号 id vs 匿名设备 id
# ---------------------------------------------------------------------------
#
# user_store.py 产生的账号 id 命名空间(逐一核对过):
#   apple:<sub>      Sign in with Apple
#   phone:<手机号>    手机号 + 验证码
#   pw:<邮箱|手机号>  邮箱/手机号 + 密码
#   wechat:demo      演示用微信登录(全体共享的演示账号)
# 匿名设备 id 是 iOS identifierForVendor(UUID,只含十六进制和 '-')。
# 判定规则:含 ':'(任何带命名空间前缀的 id,包括将来新增的)或含 '@'(邮箱样式)
# 一律当账号处理;其它当匿名设备 id。宁可多拦,不可漏拦。


def is_account_id(user_id: str | None) -> bool:
    return bool(user_id) and (":" in user_id or "@" in user_id)


def _uid_fingerprint(user_id: str) -> dict:
    """日志里不写明文手机号/邮箱,只写命名空间 + 哈希前缀,够关联排查用。"""
    ns = user_id.split(":", 1)[0] if ":" in user_id else ("email" if "@" in user_id else "anon")
    return {"ns": ns, "uid_sha": hashlib.sha256(user_id.encode()).hexdigest()[:12]}


# ---------------------------------------------------------------------------
# 客户端 IP:隧道流量的套接字对端是本机,真实 IP 在 CF-Connecting-IP
# ---------------------------------------------------------------------------


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _scope_header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or ():
        if k == name:
            try:
                return v.decode("latin-1").strip()
            except Exception:  # noqa: BLE001
                return None
    return None


def client_ip_from_scope(scope) -> str:
    """只有套接字对端是本机(= cloudflared 隧道转进来的)时才信 CF-Connecting-IP;
    直连 8000 端口的请求一律用套接字对端 IP——否则任何人伪造一个
    CF-Connecting-IP 头就能无限换"身份"绕过限流。"""
    client = scope.get("client")
    peer = client[0] if client else None
    if is_loopback(peer):
        cf = _scope_header(scope, b"cf-connecting-ip")
        if cf:
            try:
                return str(ipaddress.ip_address(cf))
            except ValueError:
                pass  # 垃圾值不当 key,退回对端地址
    return peer or "unknown"


def client_ip(request: Request) -> str:
    return client_ip_from_scope(request.scope)


def is_local_operator_request(request: Request) -> bool:
    """运维接口(/auth/enforcement-stats)只回答"真·本机"请求。

    注意隧道流量的对端同样是 127.0.0.1!区分办法:cloudflared 转发的请求一定带
    CF-Connecting-IP(Cloudflare 边缘强制写入,客户端改不掉),本机 curl 不带。"""
    client = request.scope.get("client")
    if not is_loopback(client[0] if client else None):
        return False
    for h in (b"cf-connecting-ip", b"x-forwarded-for", b"cf-ray"):
        if _scope_header(request.scope, h):
            return False
    return True


def user_bucket_key(user_id: str, scope, ip: str) -> str:
    """限流用的"每用户" key(见 ratelimit.RateLimitMiddleware 的说明)。"""
    if not is_account_id(user_id):
        return user_id
    tok = bearer_token(_scope_header(scope, b"authorization"))
    payload = verify_session_token(tok) if tok else None
    if payload and payload.get("sub") == user_id:
        return user_id
    return f"{user_id}@{ip}"


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


class EnforcementStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.started_at = int(time.time())
            self.checked = 0          # 账号 id 请求被检查的次数
            self.ok = 0
            self.anonymous = 0        # 匿名 id,直接放行
            self.violations: dict[str, int] = {}
            self.by_route: dict[str, int] = {}
            self.blocked = 0          # enforce 模式下真正拒绝的次数

    def record(self, route: str, reason: str | None, *, anonymous: bool = False, blocked: bool = False) -> None:
        with self._lock:
            if anonymous:
                self.anonymous += 1
                return
            self.checked += 1
            if reason is None:
                self.ok += 1
                return
            self.violations[reason] = self.violations.get(reason, 0) + 1
            self.by_route[route] = self.by_route.get(route, 0) + 1
            if blocked:
                self.blocked += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "since": self.started_at,
                "checked": self.checked,
                "ok": self.ok,
                "anonymous": self.anonymous,
                "violations_total": sum(self.violations.values()),
                "violations": dict(self.violations),
                "violations_by_route": dict(self.by_route),
                "blocked": self.blocked,
            }


stats = EnforcementStats()


def posture() -> dict:
    """安全开关的当前状态,全部是布尔/枚举——绝不包含密钥。"""
    from app.services import ratelimit

    return {
        "auth_enforce_mode": enforce_mode(),
        "demo_mode": demo_mode_enabled(),
        "jwt_secret_is_default": jwt_secret_is_default(),
        "tokens_trustworthy": tokens_trustworthy(),
        "rate_limit_enabled": ratelimit.enabled(),
    }


def log_security_posture() -> None:
    """启动时打一行安全状态(布尔值)。默认密钥 + enforce 时打 ERROR。"""
    p = posture()
    log.warning("security_posture %s", json.dumps(p, sort_keys=True))
    if p["jwt_secret_is_default"]:
        if p["auth_enforce_mode"] == "enforce":
            log.error(
                "AUTH_ENFORCE_MODE=enforce but LIONPICK_JWT_SECRET is the public default — "
                "ALL session tokens are rejected (fail closed). Set a real secret."
            )
        else:
            log.warning(
                "LIONPICK_JWT_SECRET is the public default — session JWTs are forgeable; "
                "do NOT switch AUTH_ENFORCE_MODE to enforce until a real secret is set."
            )


# ---------------------------------------------------------------------------
# 依赖:get_caller → Caller.authorize(user_id)
# ---------------------------------------------------------------------------


class Caller:
    """一次请求的调用方凭证(Authorization 头 + 来源 IP)。"""

    def __init__(self, authorization: str | None, ip: str) -> None:
        self.authorization = authorization
        self.ip = ip

    def authorize(self, user_id: str, *, route: str) -> None:
        """账号 id 必须由其本人(JWT sub)访问;匿名设备 id 放行。

        report 模式只记录不拦截;enforce 模式 401/403。"""
        mode = enforce_mode()
        if mode == "off":
            return
        if not is_account_id(user_id):
            stats.record(route, None, anonymous=True)
            return

        reason: str | None = None
        token = bearer_token(self.authorization)
        if token is None:
            reason = "missing_token"
        else:
            payload = verify_session_token(token)
            if payload is None:
                reason = "untrusted_default_secret" if not tokens_trustworthy() else "invalid_token"
            elif payload.get("sub") != user_id:
                reason = "wrong_sub"

        blocking = reason is not None and mode == "enforce"
        stats.record(route, reason, blocked=blocking)
        if reason is None:
            return
        log.warning(
            "auth_enforce_violation %s",
            json.dumps(
                {"mode": mode, "route": route, "reason": reason, "ip": self.ip,
                 "action": "blocked" if blocking else "allowed", **_uid_fingerprint(user_id)},
                sort_keys=True,
            ),
        )
        if blocking:
            if reason == "wrong_sub":
                raise HTTPException(status_code=403, detail="forbidden")
            if reason == "missing_token":
                raise HTTPException(status_code=401, detail="missing session token")
            raise HTTPException(status_code=401, detail="invalid or expired session")


def get_caller(request: Request, authorization: str | None = Header(default=None)) -> Caller:
    return Caller(authorization, client_ip(request))


# 进程启动(首次导入)时打一行安全状态;uvicorn 在导入 app 之前已经配好日志。
log_security_posture()
