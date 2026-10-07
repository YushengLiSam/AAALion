"""P0.0 复审补充测试:限流器防"灌 key"绕过、chat 中间件预检、生产真实的代理链
(uvicorn ProxyHeadersMiddleware + cloudflared)、空 JWT 密钥、注册限流、DEMO_MODE 告警。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import security
from app.routes import auth as auth_route
from app.services import ratelimit
from app.services import user_store as us
from app.services.ratelimit import RateLimitMiddleware, SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def with_peer(app, host: str):
    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(host, 40000))
        await app(scope, receive, send)

    return wrapped


# ---------------------------------------------------------------------------
# 限流器:被拒绝的请求不建 key;每个 bucket 独立 LRU
# ---------------------------------------------------------------------------


def test_blocked_requests_do_not_create_keys_or_evict_victim():
    """已被封的 IP 换着账号狂刷:不能借此灌满 LRU、把受害账号的计数挤掉。"""
    lim = SlidingWindowLimiter(max_keys=5, clock=FakeClock())
    for _ in range(2):
        assert lim.hit_many([("acct", "victim", 3, 600.0)])[0]
    assert lim.hit_many([("ip", "atk", 1, 600.0)])[0]          # 攻击者 IP 用完额度
    for i in range(1000):
        ok, _, bucket = lim.hit_many([("ip", "atk", 1, 600.0), ("acct", f"x{i}", 3, 600.0)])
        assert not ok and bucket == "ip"
    st = lim.stats()
    assert st["tracked_keys_by_bucket"]["acct"] == 1 and st["evicted_keys"] == 0
    # 受害账号的计数还在:再放行 1 次就满。
    assert lim.hit_many([("acct", "victim", 3, 600.0)])[0]
    assert not lim.hit_many([("acct", "victim", 3, 600.0)])[0]


def test_lru_is_per_bucket():
    """刷 chat_user 的新 key(被放行的)挤不掉 login_account 的计数。"""
    lim = SlidingWindowLimiter(max_keys=10, clock=FakeClock())
    for _ in range(3):
        assert lim.hit_many([("login_account", "phone:138", 3, 600.0)])[0]
    for i in range(500):
        assert lim.hit_many([("chat_user", f"u{i}", 30, 60.0)])[0]
    assert not lim.hit_many([("login_account", "phone:138", 3, 600.0)])[0]
    st = lim.stats()
    assert st["tracked_keys_by_bucket"] == {"login_account": 1, "chat_user": 10}


def test_retry_after_if_full_is_read_only():
    clk = FakeClock()
    lim = SlidingWindowLimiter(clock=clk)
    assert lim.retry_after_if_full("b", "k", 2, 60.0) == 0
    assert lim.stats()["tracked_keys"] == 0                     # 预检不建 key
    lim.hit_many([("b", "k", 2, 60.0)])
    assert lim.retry_after_if_full("b", "k", 2, 60.0) == 0
    lim.hit_many([("b", "k", 2, 60.0)])
    clk.t += 10
    assert lim.retry_after_if_full("b", "k", 2, 60.0) == 50
    clk.t += 50.5
    assert lim.retry_after_if_full("b", "k", 2, 60.0) == 0


def test_bad_max_keys_env_does_not_break_import(monkeypatch):
    monkeypatch.setenv("RL_MAX_KEYS", "lots")
    assert SlidingWindowLimiter().stats()["max_keys_per_bucket"] == 50000
    monkeypatch.setenv("RL_MAX_KEYS", "0")
    assert SlidingWindowLimiter().stats()["max_keys_per_bucket"] == 50000


def test_chat_middleware_rejects_over_limit_ip_before_reading_body(monkeypatch):
    """IP 已超限时,中间件不读请求体就返回 429(防止被超限 IP 用大请求体占内存)。"""
    monkeypatch.setenv("RL_CHAT_PER_IP", "1/1m")
    reached = []

    async def endpoint(scope, receive, send):
        reached.append(1)
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = RateLimitMiddleware(endpoint)
    scope = {"type": "http", "method": "POST", "path": "/chat/stream",
             "client": ("198.51.100.20", 1), "headers": []}

    async def run(receive):
        sent = []

        async def send(msg):
            sent.append(msg)

        await mw(dict(scope), receive, send)
        return sent

    async def ok_receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def must_not_read():
        raise AssertionError("body was read for an over-limit IP")

    first = asyncio.run(run(ok_receive))
    assert first[0]["status"] == 200 and reached == [1]
    second = asyncio.run(run(must_not_read))
    assert second[0]["status"] == 429 and reached == [1]
    assert (b"retry-after", b"60") in [(k.lower(), v) for k, v in second[0]["headers"]]


# ---------------------------------------------------------------------------
# 生产真实的代理链:uvicorn 默认 proxy_headers=True、FORWARDED_ALLOW_IPS=127.0.0.1,
# 会在 app 之前用 X-Forwarded-For(Cloudflare 追加的最右一项)改写 scope["client"]。
# ---------------------------------------------------------------------------


@pytest.fixture
def prod_stack(tmp_path, monkeypatch):
    us._reset_for_tests()
    us._store = None
    monkeypatch.setattr(us, "DB_PATH", tmp_path / "users.db")
    us.init_schema()
    app = FastAPI()
    app.include_router(auth_route.router)

    @app.get("/_probe_ip")
    async def probe(request: Request) -> dict:
        return {"ip": security.client_ip(request)}

    def stack(peer: str):
        # 与 uvicorn 0.30.6 默认行为一致:只信任 127.0.0.1 发来的 X-Forwarded-For。
        return TestClient(with_peer(ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1"), peer))

    yield stack
    us._reset_for_tests()
    us._store = None


def test_prod_tunnel_request_keyed_by_real_client_ip(prod_stack):
    c = prod_stack("127.0.0.1")
    # cloudflared 转发:CF-Connecting-IP + X-Forwarded-For(Cloudflare 把真实 IP 追加在最右)。
    h = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "203.0.113.50"}
    assert c.get("/_probe_ip", headers=h).json()["ip"] == "203.0.113.50"
    # 客户端自带的 XFF 前缀被 Cloudflare 追加后,最右一项仍是真实 IP——伪造无效。
    h = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "1.1.1.1, 203.0.113.50"}
    assert c.get("/_probe_ip", headers=h).json()["ip"] == "203.0.113.50"


def test_prod_direct_request_cannot_spoof_forwarding_headers(prod_stack):
    c = prod_stack("198.51.100.77")       # 直连 8000 端口
    h = {"CF-Connecting-IP": "203.0.113.1", "X-Forwarded-For": "203.0.113.2"}
    assert c.get("/_probe_ip", headers=h).json()["ip"] == "198.51.100.77"


def test_prod_ipv6_loopback_tunnel_uses_cf_header(prod_stack):
    """cloudflared 的 localhost 若解析成 ::1(后端改成监听 :: 时),uvicorn 不信任 ::1 的
    XFF,不改写对端;此时由 app.security 的 CF-Connecting-IP 分支兜住。"""
    c = prod_stack("::1")
    h = {"CF-Connecting-IP": "2001:db8::5", "X-Forwarded-For": "2001:db8::5"}
    assert c.get("/_probe_ip", headers=h).json()["ip"] == "2001:db8::5"


def test_prod_enforcement_stats_local_only(prod_stack):
    assert prod_stack("127.0.0.1").get("/auth/enforcement-stats").status_code == 200
    tunnel = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "203.0.113.50", "CF-Ray": "x"}
    assert prod_stack("127.0.0.1").get("/auth/enforcement-stats", headers=tunnel).status_code == 404
    assert prod_stack("::1").get("/auth/enforcement-stats", headers=tunnel).status_code == 404
    assert prod_stack("198.51.100.77").get("/auth/enforcement-stats").status_code == 404


def test_prod_sms_ip_limit_separates_tunnel_users(prod_stack, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("RL_SMS_PER_IP", "2/10m")
    c = prod_stack("127.0.0.1")
    a = {"CF-Connecting-IP": "203.0.113.60", "X-Forwarded-For": "203.0.113.60"}
    b = {"CF-Connecting-IP": "203.0.113.61", "X-Forwarded-For": "203.0.113.61"}
    for i in range(2):
        assert c.post("/auth/phone/start", json={"phone": f"1360000000{i}"}, headers=a).status_code == 503
    assert c.post("/auth/phone/start", json={"phone": "13600000009"}, headers=a).status_code == 429
    assert c.post("/auth/phone/start", json={"phone": "13600000008"}, headers=b).status_code == 503


# ---------------------------------------------------------------------------
# 注册限流
# ---------------------------------------------------------------------------


def test_register_is_rate_limited_per_ip(prod_stack, monkeypatch):
    monkeypatch.setenv("RL_REGISTER_PER_IP", "2/10m")
    c = prod_stack("198.51.100.30")
    for i in range(2):
        r = c.post("/auth/register", json={"identifier": f"u{i}@example.com", "password": "secret123"})
        assert r.status_code == 200
    r = c.post("/auth/register", json={"identifier": "u9@example.com", "password": "secret123"})
    assert r.status_code == 429 and r.json()["scope"] == "register_ip" and "Retry-After" in r.headers
    # 注册不占登录额度:同一 IP 照样能登录。
    r = c.post("/auth/password/login", json={"identifier": "u0@example.com", "password": "secret123"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 空 JWT 密钥 = 默认密钥(子进程里导入,避免污染本进程已加载的模块)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [("", True), ("   ", True), ("a-real-secret-value-123", False)])
def test_empty_jwt_secret_is_treated_as_default(value, expected):
    env = dict(os.environ, LIONPICK_JWT_SECRET=value)
    out = subprocess.run(
        [sys.executable, "-c",
         "from app.services import jwt_session as j; print(j.using_default_secret())"],
        cwd=str(SERVER_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(expected)


# ---------------------------------------------------------------------------
# DEMO_MODE=1 启动告警
# ---------------------------------------------------------------------------


def test_demo_mode_posture_warns(monkeypatch, caplog):
    monkeypatch.setenv("DEMO_MODE", "1")
    with caplog.at_level(logging.WARNING, logger="lionpick.security"):
        security.log_security_posture()
    msgs = [r.getMessage() for r in caplog.records]
    posture = next(m for m in msgs if m.startswith("security_posture "))
    assert json.loads(posture.split(" ", 1)[1])["demo_mode"] is True
    assert any("DEMO_MODE=1" in m and "does NOT protect" in m for m in msgs)


def test_no_demo_warning_when_off(monkeypatch, caplog):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    with caplog.at_level(logging.WARNING, logger="lionpick.security"):
        security.log_security_posture()
    assert not any("DEMO_MODE=1" in r.getMessage() for r in caplog.records)
