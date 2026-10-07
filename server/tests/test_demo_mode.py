"""P0.0 DEMO_MODE:验证码只在 DEMO_MODE=1 时随响应返回。

DEMO_MODE=0(代码默认)+ 本地后端:phone/start 与 password/reset/start 返回
503 + error=sms_not_configured,不生成也不保存验证码;密码登录、Apple 登录不受影响。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import auth as auth_route
from app.services import user_store as us


@pytest.fixture
def client(tmp_path, monkeypatch):
    us._reset_for_tests()
    us._store = None
    monkeypatch.setattr(us, "DB_PATH", tmp_path / "users.db")
    us.init_schema()
    app = FastAPI()
    app.include_router(auth_route.router)
    yield TestClient(app)
    us._reset_for_tests()
    us._store = None


def _sms_rows() -> int:
    return us._connection().execute("SELECT COUNT(*) FROM sms_codes").fetchone()[0]


def test_default_is_off(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert us.demo_mode_enabled() is False
    for v in ("0", "", "false", "no", "2"):
        monkeypatch.setenv("DEMO_MODE", v)
        assert us.demo_mode_enabled() is False
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("DEMO_MODE", v)
        assert us.demo_mode_enabled() is True


def test_phone_start_without_demo_mode_does_not_leak_code(client, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    r = client.post("/auth/phone/start", json={"phone": "13800138000"})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "sms_not_configured"
    assert "dev_code" not in body and body["detail"]
    assert all(isinstance(v, str) for v in body.values())   # 老 iOS 能取到 detail 显示
    assert _sms_rows() == 0                                  # 连存都没存
    # 没有码可猜:verify 必然失败。
    assert client.post("/auth/phone/verify", json={"phone": "13800138000", "code": "000000"}).status_code == 400


def test_reset_start_without_demo_mode_same_answer_for_any_account(client, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    client.post("/auth/register", json={"identifier": "dave@example.com", "password": "secret123"})
    a = client.post("/auth/password/reset/start", json={"identifier": "dave@example.com"})
    b = client.post("/auth/password/reset/start", json={"identifier": "nobody@example.com"})
    assert a.status_code == b.status_code == 503
    assert a.json()["error"] == b.json()["error"] == "sms_not_configured"
    assert "dev_code" not in a.json()
    # 格式错误仍然是 400(先于 DEMO_MODE 判断)。
    assert client.post("/auth/password/reset/start", json={"identifier": "not-an-id"}).status_code == 400


def test_password_and_apple_login_unaffected(client, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert client.post("/auth/register", json={"identifier": "erin@example.com", "password": "secret123"}).status_code == 200
    r = client.post("/auth/password/login", json={"identifier": "erin@example.com", "password": "secret123"})
    assert r.status_code == 200 and r.json()["jwt"]
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "000999.apple"}).encode()).rstrip(b"=").decode()
    r = client.post("/auth/apple", json={"identity_token": f"h.{payload}.s"})
    assert r.status_code == 200 and r.json()["user_id"] == "apple:000999.apple"


def test_demo_mode_on_restores_dev_code(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    r = client.post("/auth/phone/start", json={"phone": "13800138001"})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is True and body["demo"] is True and len(body["dev_code"]) == 6
    v = client.post("/auth/phone/verify", json={"phone": "13800138001", "code": body["dev_code"]})
    assert v.status_code == 200 and v.json()["user_id"] == "phone:13800138001"

    client.post("/auth/register", json={"identifier": "fay@example.com", "password": "secret123"})
    r = client.post("/auth/password/reset/start", json={"identifier": "fay@example.com"})
    assert r.status_code == 200 and len(r.json()["dev_code"]) == 6
