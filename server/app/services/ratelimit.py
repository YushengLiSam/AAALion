"""进程内限流器(P0.0 安全止血)——零新依赖。

为什么自己写而不是 slowapi:生产只有一个 uvicorn worker,进程内滑动窗口就够用;
不引入新依赖也就不用动 VM 上锁死的 requirements。

算法:滑动窗口日志(sliding window log)。每个 (bucket, key) 保存最近一个窗口内
"被放行"请求的时间戳(deque)。被拒绝的请求不记录,避免攻击者把自己越锁越久。

  * 线程安全:一把 threading.Lock 保护全部状态(临界区只有几次 deque 操作)。
  * 内存有界:OrderedDict 做 LRU,超过 max_keys 时淘汰最久未访问的 key;每个 key
    最多存 limit 个时间戳(limit 都是两位数),所以总内存 ≈ max_keys × limit。
  * hit_many():一次请求要同时满足多个桶(如 "每 IP" + "每手机号")时,先在锁内
    全部检查,全部通过才一起记账——不会出现 "IP 桶扣了次数但手机号桶拒绝" 的半扣。

**注意:限额是"每进程"的**。今天线上是单 uvicorn worker,所以等价于全局限额;
将来如果开多 worker / 多机,要换 Redis 之类的共享存储(见 docs/SECURITY.md)。

配置(环境变量,格式 "次数/窗口",窗口可写 600、600s、10m、1h):
  RATE_LIMIT_ENABLED       默认 1;设 0 整体关闭(紧急回滚用)
  RL_SMS_PER_TARGET        默认 3/10m   /auth/phone/start + 密码重置 start,每手机号/账号
  RL_SMS_PER_IP            默认 10/10m  同上,每 IP
  RL_LOGIN_PER_IP          默认 20/10m  登录 / 验证码校验 / 重置校验 / 改密,每 IP
  RL_LOGIN_PER_ACCOUNT     默认 10/10m  同上,每账号
  RL_CHAT_PER_IP           默认 30/1m   /chat/stream,每 IP
  RL_CHAT_PER_USER         默认 30/1m   /chat/stream,每 user_id
  RL_MAX_KEYS              默认 50000   LRU 上限
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Iterable

from starlette.responses import JSONResponse

# bucket 名 → (环境变量名, 默认值)。bucket 名同时出现在 429 响应和统计里。
LIMIT_DEFAULTS: dict[str, tuple[str, str]] = {
    "sms_target": ("RL_SMS_PER_TARGET", "3/10m"),
    "sms_ip": ("RL_SMS_PER_IP", "10/10m"),
    "login_ip": ("RL_LOGIN_PER_IP", "20/10m"),
    "login_account": ("RL_LOGIN_PER_ACCOUNT", "10/10m"),
    "chat_ip": ("RL_CHAT_PER_IP", "30/1m"),
    "chat_user": ("RL_CHAT_PER_USER", "30/1m"),
}

_SPEC_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*([smh]?)\s*$", re.IGNORECASE)
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_limit(spec: str) -> tuple[int, float]:
    """"3/10m" → (3, 600.0)。格式不对抛 ValueError。"""
    m = _SPEC_RE.match(spec or "")
    if not m:
        raise ValueError(f"bad rate-limit spec: {spec!r}")
    n, w, unit = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if n <= 0 or w <= 0:
        raise ValueError(f"bad rate-limit spec: {spec!r}")
    return n, float(w * _UNIT[unit])


def limit_for(bucket: str) -> tuple[int, float]:
    """每次调用时读环境变量(便宜;测试可以 monkeypatch)。配错了退回默认值,
    不能因为一个错别字把限流整个关掉。"""
    env, default = LIMIT_DEFAULTS[bucket]
    raw = os.getenv(env, default)
    try:
        return parse_limit(raw)
    except ValueError:
        return parse_limit(default)


def _fmt(limit: tuple[int, float]) -> str:
    return f"{limit[0]}/{int(limit[1])}s"


def enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


class SlidingWindowLimiter:
    """线程安全、内存有界的滑动窗口日志限流器。"""

    def __init__(self, max_keys: int | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_keys = max_keys or int(os.getenv("RL_MAX_KEYS", "50000"))
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: "OrderedDict[tuple[str, str], deque[float]]" = OrderedDict()
        self._blocked: dict[str, int] = {}
        self._allowed = 0
        self._evicted = 0

    def _window(self, k: tuple[str, str], now: float, window: float) -> "deque[float]":
        dq = self._hits.get(k)
        if dq is None:
            dq = deque()
            self._hits[k] = dq
            if len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)   # LRU 淘汰最久没访问的 key
                self._evicted += 1
        else:
            self._hits.move_to_end(k)
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        return dq

    def hit_many(self, checks: Iterable[tuple[str, str, int, float]]) -> tuple[bool, int, str | None]:
        """checks = [(bucket, key, limit, window_sec), ...]。

        全部桶都有余量 → 一起记账,返回 (True, 0, None);
        任一桶已满 → 不记账,返回 (False, retry_after_秒, 触发的 bucket)。
        retry_after 取所有已满桶里最长的等待,向上取整且 ≥1。"""
        checks = [c for c in checks if c[1]]
        now = self._clock()
        with self._lock:
            worst_wait, worst_bucket = 0.0, None
            windows = []
            for bucket, key, limit, window in checks:
                dq = self._window((bucket, key), now, window)
                windows.append(dq)
                if len(dq) >= limit:
                    # 最早那次放行滑出窗口后才有新名额。
                    wait = dq[len(dq) - limit] + window - now
                    if wait >= worst_wait:
                        worst_wait, worst_bucket = wait, bucket
            if worst_bucket is not None:
                self._blocked[worst_bucket] = self._blocked.get(worst_bucket, 0) + 1
                return False, max(1, math.ceil(worst_wait)), worst_bucket
            for dq in windows:
                dq.append(now)
            self._allowed += 1
            return True, 0, None

    def stats(self) -> dict:
        with self._lock:
            return {
                "enabled": enabled(),
                "scope": "per-process",
                "tracked_keys": len(self._hits),
                "max_keys": self._max_keys,
                "evicted_keys": self._evicted,
                "allowed": self._allowed,
                "blocked": dict(self._blocked),
                "limits": {b: _fmt(limit_for(b)) for b in LIMIT_DEFAULTS},
            }

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._blocked.clear()
            self._allowed = 0
            self._evicted = 0


# 进程级单例(单 uvicorn worker → 等价全局)。
limiter = SlidingWindowLimiter()


def too_many_response(retry_after: int, bucket: str | None) -> JSONResponse:
    """429 + Retry-After。body 里所有值都是字符串:老版本 iOS 客户端把错误 body
    解成 [String: String] 取 detail,混入数字会解码失败、只显示 "HTTP 429"。"""
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"请求过于频繁,请 {retry_after} 秒后再试 / too many requests, retry in {retry_after}s",
            "error": "rate_limited",
            "scope": bucket or "",
            "retry_after": str(retry_after),
        },
        headers={"Retry-After": str(retry_after)},
    )


def check(pairs: Iterable[tuple[str, str]]) -> JSONResponse | None:
    """路由层用:pairs = [(bucket, key), ...]。放行返回 None,超限返回 429 响应。"""
    if not enabled():
        return None
    checks = [(b, k, *limit_for(b)) for b, k in pairs]
    ok, retry, bucket = limiter.hit_many(checks)
    return None if ok else too_many_response(retry, bucket)


# ---------------------------------------------------------------------------
# ASGI 中间件:/chat/stream 每 IP + 每用户限流(不改 chat.py)
# ---------------------------------------------------------------------------

_MAX_PEEK_BODY = 16 * 1024 * 1024  # 超过这个大小不解析 user_id(只按 IP 限)


def _extract_user_id(body: bytes) -> str | None:
    if not body or len(body) > _MAX_PEEK_BODY or body.lstrip()[:1] != b"{":
        return None
    try:
        uid = json.loads(body).get("user_id")
    except Exception:  # noqa: BLE001 — 解析失败交给路由自己报 422
        return None
    if isinstance(uid, str) and 0 < len(uid) <= 128:
        return uid
    return None


class RateLimitMiddleware:
    """纯 ASGI 中间件(不用 BaseHTTPMiddleware,避免影响 SSE 流式响应)。

    只拦 POST /chat/stream:先把请求体完整读出来(FastAPI 反正也要读),取出
    user_id 做每用户限流,再把请求体原样"回放"给下游;回放完后的 receive 交还给
    原始通道,StreamingResponse 依旧能感知客户端断开。

    每用户 key 的防冒用:匿名 UUID 不可猜,直接按 user_id 计;账号 id(phone:… 等可
    枚举)只有带了 sub 匹配的有效 JWT 才按账号计,否则按 "账号@IP" 计——这样攻击者
    冒填别人的手机号账号只会耗尽自己 IP 下的额度,锁不住真正的用户。"""

    def __init__(self, app, paths: tuple[str, ...] = ("/chat/stream",)) -> None:
        self.app = app
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in self.paths
            or not enabled()
        ):
            await self.app(scope, receive, send)
            return

        from app.security import client_ip_from_scope, user_bucket_key  # 避免循环导入

        chunks: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)

        ip = client_ip_from_scope(scope)
        pairs = [("chat_ip", ip)]
        uid = _extract_user_id(body)
        if uid:
            pairs.append(("chat_user", user_bucket_key(uid, scope, ip)))
        blocked = check(pairs)
        if blocked is not None:
            await blocked(scope, receive, send)
            return

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
