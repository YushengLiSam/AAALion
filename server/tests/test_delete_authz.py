"""Regression test for the /auth/delete authorization fix (pre-freeze audit).

Before: any caller could destroy any non-password account (phone/apple/wechat)
by POSTing a guessable user_id — no credentials. Now delete requires a valid
session JWT whose subject == user_id (owner-only); password accounts must ALSO
supply their password.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

os.environ.pop("LIONPICK_JWT_SECRET", None)  # default secret → jwt readable from login

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import auth as auth_route
from app.services import user_store as us


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # The store keys off the module-level DB_PATH + a cached connection, not an
    # env var — point both at an isolated temp DB and clear any stale state a
    # prior test module may have left (else we open against a deleted tmp dir).
    us._reset_for_tests()
    us._store = None
    us.DB_PATH = tmp_path_factory.mktemp("authz") / "users.db"
    us.init_schema()
    app = FastAPI()
    app.include_router(auth_route.router)
    yield TestClient(app)
    us._reset_for_tests()
    us._store = None


def _reg_phone(client, num):
    code = client.post("/auth/phone/start", json={"phone": num}).json()["dev_code"]
    return client.post("/auth/phone/verify", json={"phone": num, "code": code}).json()


def _delete(client, uid, auth=None, pw=None):
    body = {"user_id": uid}
    if pw is not None:
        body["password"] = pw
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    return client.post("/auth/delete", json=body, headers=headers).status_code


def test_anonymous_delete_rejected(client):
    victim = _reg_phone(client, "13800138001")
    assert _delete(client, victim["user_id"]) == 401
    assert client.get("/auth/me", params={"user_id": victim["user_id"]}).status_code == 200


def test_delete_with_other_users_token_forbidden(client):
    victim = _reg_phone(client, "13800138002")
    attacker = _reg_phone(client, "13900139002")
    assert _delete(client, victim["user_id"], auth=attacker["jwt"]) == 403
    assert client.get("/auth/me", params={"user_id": victim["user_id"]}).status_code == 200


def test_garbage_token_rejected(client):
    victim = _reg_phone(client, "13800138003")
    assert _delete(client, victim["user_id"], auth="not.a.jwt") == 401


def test_owner_can_delete_self(client):
    victim = _reg_phone(client, "13800138004")
    assert _delete(client, victim["user_id"], auth=victim["jwt"]) == 200
    assert client.get("/auth/me", params={"user_id": victim["user_id"]}).status_code == 404


def test_password_account_needs_jwt_and_password(client):
    client.post("/auth/register", json={"identifier": "alice@example.com", "password": "secret123"})
    acct = client.post("/auth/password/login",
                       json={"identifier": "alice@example.com", "password": "secret123"}).json()
    # valid jwt but wrong password -> still blocked
    assert _delete(client, acct["user_id"], auth=acct["jwt"], pw="wrongpw") == 400
    # valid jwt + correct password -> deleted
    assert _delete(client, acct["user_id"], auth=acct["jwt"], pw="secret123") == 200
