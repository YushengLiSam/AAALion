"""P0.0 限流测试:滑动窗口算法、客户端 IP 识别(隧道 vs 直连)、各接口限额、
429 + Retry-After、/chat/stream 中间件(含请求体回放)、运维统计接口仅本机可见。"""

from __future__ import annotations

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

from app import security
from app.routes import auth as auth_route
from app.services import jwt_session, ratelimit
from app.services import user_store as us
from app.services.ratelimit import RateLimitMiddleware, SlidingWindowLimiter, parse_limit


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def with_peer(app, host: str):
    """把 ASGI scope 里的套接字对端改成指定地址(TestClient 默认是 "testclient")。"""

    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(host, 40000))
        await app(scope, receive, send)

    return wrapped


# ---------------------------------------------------------------------------
# 算法
# ---------------------------------------------------------------------------


def test_parse_limit():
    assert parse_limit("3/10m") == (3, 600.0)
    assert parse_limit("20/600") == (20, 600.0)
    assert parse_limit("30 / 1m") == (30, 60.0)
    assert parse_limit("5/2h") == (5, 7200.0)
    for bad in ("", "abc", "0/10", "3/0", "-1/5"):
        with pytest.raises(ValueError):
            parse_limit(bad)


def test_bad_env_spec_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RL_SMS_PER_TARGET", "garbage")
    assert ratelimit.limit_for("sms_target") == (3, 600.0)


def test_sliding_window_blocks_then_recovers():
    clk = FakeClock()
    lim = SlidingWindowLimiter(clock=clk)
    for _ in range(3):
        assert lim.hit_many([("b", "k", 3, 60.0)])[0]
    ok, retry, bucket = lim.hit_many([("b", "k", 3, 60.0)])
    assert not ok and bucket == "b" and retry == 60
    clk.t += 30
    ok, retry, _ = lim.hit_many([("b", "k", 3, 60.0)])
    assert not ok and retry == 30            # 最早那次放行还要 30s 才滑出窗口
    clk.t += 30.5
    assert lim.hit_many([("b", "k", 3, 60.0)])[0]
    assert lim.hit_many([("b", "other", 3, 60.0)])[0]   # 不同 key 互不影响


def test_hit_many_is_all_or_nothing():
    clk = FakeClock()
    lim = SlidingWindowLimiter(clock=clk)
    assert lim.hit_many([("acct", "a", 1, 60.0)])[0]
    # acct 已满:ip 桶不应被扣次数。
    for _ in range(5):
        assert not lim.hit_many([("ip", "1.2.3.4", 2, 60.0), ("acct", "a", 1, 60.0)])[0]
    assert lim.hit_many([("ip", "1.2.3.4", 2, 60.0)])[0]
    assert lim.hit_many([("ip", "1.2.3.4", 2, 60.0)])[0]
    assert not lim.hit_many([("ip", "1.2.3.4", 2, 60.0)])[0]


def test_memory_is_bounded_by_lru_eviction():
    lim = SlidingWindowLimiter(max_keys=100, clock=FakeClock())
    for i in range(1000):
        lim.hit_many([("b", f"k{i}", 5, 60.0)])
    st = lim.stats()
    assert st["tracked_keys"] == 100 and st["evicted_keys"] == 900


def test_thread_safety_exact_count():
    import threading

    lim = SlidingWindowLimiter(clock=FakeClock())
    allowed = []

    def worker():
        for _ in range(50):
            if lim.hit_many([("b", "shared", 100, 60.0)])[0]:
                allowed.append(1)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(allowed) == 100


# ---------------------------------------------------------------------------
# 客户端 IP:只有本机对端(隧道)才信 CF-Connecting-IP
# ---------------------------------------------------------------------------


def _scope(peer, headers=()):
    return {"client": (peer, 1) if peer else None,
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}


def test_client_ip_keying():
    f = security.client_ip_from_scope
    assert f(_scope("127.0.0.1", [("CF-Connecting-IP", "203.0.113.7")])) == "203.0.113.7"
    assert f(_scope("::1", [("CF-Connecting-IP", "2001:db8::1")])) == "2001:db8::1"
    assert f(_scope("::ffff:127.0.0.1", [("CF-Connecting-IP", "203.0.113.8")])) == "203.0.113.8"
    # 直连:伪造的 CF 头一律无视。
    assert f(_scope("198.51.100.9", [("CF-Connecting-IP", "203.0.113.7")])) == "198.51.100.9"
    # 隧道但 CF 头是垃圾 → 退回对端地址。
    assert f(_scope("127.0.0.1", [("CF-Connecting-IP", "not-an-ip")])) == "127.0.0.1"
    assert f(_scope("127.0.0.1")) == "127.0.0.1"
    assert f(_scope(None)) == "unknown"


# ---------------------------------------------------------------------------
# /auth/* 接口
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    us._reset_for_tests()
    us._store = None
    monkeypatch.setattr(us, "DB_PATH", tmp_path / "users.db")
    us.init_schema()
    monkeypatch.setenv("DEMO_MODE", "1")
    app = FastAPI()
    app.include_router(auth_route.router)
    yield app
    us._reset_for_tests()
    us._store = None


def test_sms_start_per_phone_limit_and_retry_after(auth_app):
    c = TestClient(auth_app)
    for _ in range(3):
        assert c.post("/auth/phone/start", json={"phone": "13800000001"}).status_code == 200
    r = c.post("/auth/phone/start", json={"phone": "13800000001"})
    assert r.status_code == 429
    assert 1 <= int(r.headers["Retry-After"]) <= 600
    body = r.json()
    assert body["error"] == "rate_limited" and body["scope"] == "sms_target"
    assert body["retry_after"] == r.headers["Retry-After"]
    assert all(isinstance(v, str) for v in body.values())   # 老 iOS 的 [String:String] 解码
    # 别的手机号不受影响。
    assert c.post("/auth/phone/start", json={"phone": "13800000002"}).status_code == 200


def test_sms_start_per_ip_limit(auth_app):
    c = TestClient(auth_app)
    for i in range(10):
        assert c.post("/auth/phone/start", json={"phone": f"1380000{i:04d}"}).status_code == 200
    r = c.post("/auth/phone/start", json={"phone": "13899999999"})
    assert r.status_code == 429 and r.json()["scope"] == "sms_ip"


def test_reset_start_shares_sms_limits(auth_app):
    c = TestClient(auth_app)
    c.post("/auth/register", json={"identifier": "bob@example.com", "password": "secret123"})
    for ident in ("bob@example.com", "BOB@example.com ", "bob@example.com"):
        assert c.post("/auth/password/reset/start", json={"identifier": ident}).status_code == 200
    r = c.post("/auth/password/reset/start", json={"identifier": "bob@example.com"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_tunnel_clients_are_keyed_by_cf_connecting_ip(auth_app):
    c = TestClient(with_peer(auth_app, "127.0.0.1"))
    for i in range(10):
        assert c.post("/auth/phone/start", json={"phone": f"1370000{i:04d}"},
                      headers={"CF-Connecting-IP": "203.0.113.10"}).status_code == 200
    assert c.post("/auth/phone/start", json={"phone": "13711111111"},
                  headers={"CF-Connecting-IP": "203.0.113.10"}).status_code == 429
    # 隧道后面的另一个真实用户不受牵连。
    assert c.post("/auth/phone/start", json={"phone": "13722222222"},
                  headers={"CF-Connecting-IP": "203.0.113.11"}).status_code == 200


def test_direct_clients_cannot_dodge_by_spoofing_cf_header(auth_app):
    c = TestClient(with_peer(auth_app, "198.51.100.20"))
    for i in range(10):
        assert c.post("/auth/phone/start", json={"phone": f"1360000{i:04d}"},
                      headers={"CF-Connecting-IP": f"203.0.113.{i}"}).status_code == 200
    r = c.post("/auth/phone/start", json={"phone": "13633333333"},
               headers={"CF-Connecting-IP": "203.0.113.99"})
    assert r.status_code == 429 and r.json()["scope"] == "sms_ip"


def test_login_per_account_and_per_ip(auth_app, monkeypatch):
    c = TestClient(auth_app)
    c.post("/auth/register", json={"identifier": "carol@example.com", "password": "secret123"})
    for _ in range(10):
        r = c.post("/auth/password/login", json={"identifier": "carol@example.com", "password": "wrong!"})
        assert r.status_code == 400
    r = c.post("/auth/password/login", json={"identifier": "carol@example.com", "password": "secret123"})
    assert r.status_code == 429 and r.json()["scope"] == "login_account"
    # 每 IP 20 次:换账号继续撞,第 21 次被 IP 桶拦下。
    for i in range(10):
        assert c.post("/auth/password/login", json={"identifier": f"x{i}@example.com", "password": "p"}).status_code == 400
    r = c.post("/auth/password/login", json={"identifier": "y@example.com", "password": "p"})
    assert r.status_code == 429 and r.json()["scope"] == "login_ip"


def test_phone_verify_brute_force_is_capped(auth_app, monkeypatch):
    monkeypatch.setenv("RL_LOGIN_PER_ACCOUNT", "3/10m")
    c = TestClient(auth_app)
    c.post("/auth/phone/start", json={"phone": "13500000000"})
    for code in ("000000", "111111", "222222"):
        assert c.post("/auth/phone/verify", json={"phone": "13500000000", "code": code}).status_code == 400
    assert c.post("/auth/phone/verify", json={"phone": "13500000000", "code": "333333"}).status_code == 429


def test_rate_limit_kill_switch(auth_app, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    c = TestClient(auth_app)
    for _ in range(6):
        assert c.post("/auth/phone/start", json={"phone": "13400000000"}).status_code == 200


# ---------------------------------------------------------------------------
# /chat/stream 中间件
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_app():
    app = FastAPI()

    @app.post("/chat/stream")
    async def fake_stream(request: Request):
        body = await request.json()   # 中间件读过 body 后必须原样回放
        return {"echo_user": body.get("user_id"), "len": len(body.get("pad", ""))}

    @app.post("/other")
    async def other():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware)
    return app


def test_chat_body_is_replayed_intact(chat_app):
    c = TestClient(chat_app)
    pad = "x" * 300_000
    r = c.post("/chat/stream", json={"user_id": "ANON-UUID-1234", "pad": pad})
    assert r.status_code == 200 and r.json() == {"echo_user": "ANON-UUID-1234", "len": 300_000}


def test_chat_per_ip_limit(chat_app, monkeypatch):
    monkeypatch.setenv("RL_CHAT_PER_IP", "3/1m")
    c = TestClient(chat_app)
    for i in range(3):
        assert c.post("/chat/stream", json={"user_id": f"ANON-{i}-xxxxxxxx"}).status_code == 200
    r = c.post("/chat/stream", json={"user_id": "ANON-9-xxxxxxxx"})
    assert r.status_code == 429 and r.headers["Retry-After"] and r.json()["scope"] == "chat_ip"
    # 其它路径不受影响。
    for _ in range(5):
        assert c.post("/other").status_code == 200


def test_chat_per_user_limit_across_tunnel_ips(chat_app, monkeypatch):
    monkeypatch.setenv("RL_CHAT_PER_USER", "2/1m")
    c = TestClient(with_peer(chat_app, "127.0.0.1"))
    uid = "0E2C3A6F-1111-2222-3333-444455556666"   # 匿名 UUID:不可猜,直接按 user_id 计
    for ip in ("203.0.113.1", "203.0.113.2"):
        assert c.post("/chat/stream", json={"user_id": uid}, headers={"CF-Connecting-IP": ip}).status_code == 200
    r = c.post("/chat/stream", json={"user_id": uid}, headers={"CF-Connecting-IP": "203.0.113.3"})
    assert r.status_code == 429 and r.json()["scope"] == "chat_user"


def test_chat_spoofed_account_id_cannot_lock_out_victim(chat_app, monkeypatch):
    """冒填别人的账号 id(无有效 JWT)只会耗尽 "账号@攻击者IP" 的额度。"""
    monkeypatch.setenv("RL_CHAT_PER_USER", "2/1m")
    monkeypatch.setattr(jwt_session, "_SECRET", b"chat-test-secret")
    c = TestClient(with_peer(chat_app, "127.0.0.1"))
    victim = "phone:13800138000"
    attacker_ip = {"CF-Connecting-IP": "198.51.100.66"}
    for _ in range(2):
        assert c.post("/chat/stream", json={"user_id": victim}, headers=attacker_ip).status_code == 200
    assert c.post("/chat/stream", json={"user_id": victim}, headers=attacker_ip).status_code == 429
    # 真正的用户带着自己的 JWT 从别的 IP 来,不受影响;且按账号计数。
    hdr = {"CF-Connecting-IP": "203.0.113.50", "Authorization": f"Bearer {jwt_session.issue(victim)}"}
    assert c.post("/chat/stream", json={"user_id": victim}, headers=hdr).status_code == 200
    hdr2 = dict(hdr, **{"CF-Connecting-IP": "203.0.113.51"})
    assert c.post("/chat/stream", json={"user_id": victim}, headers=hdr2).status_code == 200
    assert c.post("/chat/stream", json={"user_id": victim}, headers=hdr2).status_code == 429


def test_main_app_registers_rate_limit_middleware():
    from app.main import create_app

    app = create_app()
    assert any(m.cls is RateLimitMiddleware for m in app.user_middleware)


# ---------------------------------------------------------------------------
# /auth/enforcement-stats:只回答本机运维请求
# ---------------------------------------------------------------------------


def test_enforcement_stats_is_local_only(auth_app):
    assert TestClient(auth_app).get("/auth/enforcement-stats").status_code == 404           # 非本机
    direct = TestClient(with_peer(auth_app, "198.51.100.1"))
    assert direct.get("/auth/enforcement-stats").status_code == 404
    local = TestClient(with_peer(auth_app, "127.0.0.1"))
    # 隧道流量对端也是 127.0.0.1,但带 CF 头 → 当外部请求。
    assert local.get("/auth/enforcement-stats", headers={"CF-Connecting-IP": "203.0.113.1"}).status_code == 404
    r = local.get("/auth/enforcement-stats")
    assert r.status_code == 200
    body = r.json()
    p = body["posture"]
    assert p["demo_mode"] is True and isinstance(p["jwt_secret_is_default"], bool)
    assert p["auth_enforce_mode"] in ("off", "report", "enforce")
    assert "secret" not in str({k: v for k, v in p.items() if k != "jwt_secret_is_default"}).lower()
    assert body["rate_limit"]["scope"] == "per-process"
    assert "violations" in body["auth_enforcement"]
