"""P0.0 越权(IDOR)校验回归测试——仿照 test_delete_authz.py。

每个接收 user_id 的接口 × 两种模式(report / enforce)× 四种调用方:
  * 账号 id + 不带 token
  * 账号 id + 别人的 token(sub 不符)
  * 账号 id + 本人 token
  * 匿名设备 UUID + 不带 token
enforce:401 / 403 / 放行 / 放行;report:全部放行,但前两种计入违规统计。
另测失效保护:enforce + 源码默认 JWT 密钥 → 本人 token 也一律 401。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import security
from app.routes import auth as auth_route
from app.routes import group_buy as group_buy_route
from app.routes import preferences as preferences_route
from app.routes import price_watch as price_watch_route
from app.routes import repurchase as repurchase_route
from app.services import group_buy_db, jwt_session, preferences_db, price_watch_db, repurchase_db
from app.services import user_store as us

VICTIM = "phone:13800138000"
ATTACKER = "phone:13900139000"
ANON = str(uuid.UUID(int=0x1234_5678_9ABC_DEF0_1234_5678_9ABC_DEF0)).upper()
TEST_SECRET = b"unit-test-secret-not-the-default"


def _product_id() -> str:
    idx = preferences_db._product_index()
    if not idx:
        pytest.skip("no products in data/seed")
    return sorted(idx.keys())[0]


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 所有 SQLite 都指到临时目录,绝不碰仓库 data/。
    for mod, name in (
        (preferences_db, "preferences.db"),
        (price_watch_db, "price_watch.db"),
        (repurchase_db, "repurchase.db"),
        (group_buy_db, "group_buy.db"),
    ):
        mod._reset_for_tests()
        monkeypatch.setattr(mod, "DB_PATH", tmp_path / name)
        mod.init_schema()
    us._reset_for_tests()
    us._store = None
    monkeypatch.setattr(us, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(us, "REPO_ROOT", tmp_path)  # migrate() 只会看临时目录
    us.init_schema()
    us.get_user_store()._upsert(VICTIM, "phone", None)
    us.get_user_store()._upsert(ATTACKER, "phone", None)

    monkeypatch.setattr(jwt_session, "_SECRET", TEST_SECRET)
    # 拼单接口会做汇率换算,测试里换成恒等函数,避免联网。
    monkeypatch.setattr(group_buy_route, "normalize_product_prices", lambda ps: list(ps))
    monkeypatch.setattr(price_watch_route, "normalize_product_prices", lambda ps: list(ps))

    app = FastAPI()
    for r in (auth_route, preferences_route, price_watch_route, repurchase_route, group_buy_route):
        app.include_router(r.router)
    yield TestClient(app)
    for mod in (preferences_db, price_watch_db, repurchase_db, group_buy_db):
        mod._reset_for_tests()
    us._reset_for_tests()
    us._store = None


def _hdr(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


# 每个接口:fn(client, user_id, headers) -> Response。
def _ops(client, pid):
    # 先用匿名身份开一个团,供 join 用。
    gid = client.post("/groupbuy/create", json={"user_id": "opener-" + ANON[:20], "product_id": pid}).json()["group_id"]
    # 注意:group_buy_db._gen_group_id 用 (开团人, 商品, 秒级时间戳) 生成 id,
    # 同一人同一秒对同一商品开两次团会撞主键(既有问题,不在本次范围)。这里每次换商品。
    all_pids = iter(sorted(group_buy_db._product_index().keys()))
    return {
        "preferences.feedback": lambda u, h: client.post("/preferences/feedback", json={"user_id": u, "product_id": pid, "signal": 1}, headers=h),
        "preferences.get": lambda u, h: client.get("/preferences", params={"user_id": u}, headers=h),
        "preferences.delete": lambda u, h: client.delete("/preferences", params={"user_id": u}, headers=h),
        "price_watch.watch": lambda u, h: client.post("/price_watch/watch", json={"user_id": u, "product_id": pid, "target_price_cny": 1.0}, headers=h),
        "price_watch.alerts": lambda u, h: client.get("/price_watch/alerts", params={"user_id": u}, headers=h),
        "price_watch.remove": lambda u, h: client.delete(f"/price_watch/watch/{pid}", params={"user_id": u}, headers=h),
        "repurchase.purchase": lambda u, h: client.post("/repurchase/purchase", json={"user_id": u, "product_id": pid}, headers=h),
        "repurchase.reminders": lambda u, h: client.get("/repurchase/reminders", params={"user_id": u}, headers=h),
        "groupbuy.create": lambda u, h: client.post("/groupbuy/create", json={"user_id": u, "product_id": next(all_pids)}, headers=h),
        "groupbuy.join": lambda u, h: client.post(f"/groupbuy/{gid}/join", json={"user_id": u}, headers=h),
        "groupbuy.active": lambda u, h: client.get("/groupbuy/active", params={"user_id": u}, headers=h),
        "auth.me": lambda u, h: client.get("/auth/me", params={"user_id": u}, headers=h),
        "auth.migrate": lambda u, h: client.post("/auth/migrate", json={"from_user_id": ANON, "to_user_id": u}, headers=h),
    }


ROUTES = [
    "preferences.feedback", "preferences.get", "preferences.delete",
    "price_watch.watch", "price_watch.alerts", "price_watch.remove",
    "repurchase.purchase", "repurchase.reminders",
    "groupbuy.create", "groupbuy.join", "groupbuy.active",
    "auth.me", "auth.migrate",
]


def _ok(status: int, route: str, uid: str) -> bool:
    # /auth/me 对不存在的匿名 id 返回 404——那是业务结果,不是越权拦截。
    if route == "auth.me" and uid == ANON:
        return status == 404
    return status == 200


@pytest.mark.parametrize("route", ROUTES)
def test_enforce_mode(client, monkeypatch, route):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "enforce")
    op = _ops(client, _product_id())[route]
    victim_jwt = jwt_session.issue(VICTIM)
    attacker_jwt = jwt_session.issue(ATTACKER)

    assert op(VICTIM, _hdr(None)).status_code == 401              # 不带 token
    assert op(VICTIM, _hdr("not.a.jwt")).status_code == 401        # 垃圾 token
    assert op(VICTIM, _hdr(attacker_jwt)).status_code == 403       # 别人的 token
    r = op(VICTIM, _hdr(victim_jwt))                               # 本人
    assert _ok(r.status_code, route, VICTIM), (route, r.status_code, r.text)
    r = op(ANON, _hdr(None))                                       # 匿名设备 id
    assert _ok(r.status_code, route, ANON), (route, r.status_code, r.text)

    snap = security.stats.snapshot()
    assert snap["blocked"] == 3
    assert snap["violations"] == {"missing_token": 1, "invalid_token": 1, "wrong_sub": 1}


@pytest.mark.parametrize("route", ROUTES)
def test_report_mode_allows_but_counts(client, monkeypatch, route):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "report")
    op = _ops(client, _product_id())[route]
    attacker_jwt = jwt_session.issue(ATTACKER)
    victim_jwt = jwt_session.issue(VICTIM)

    for hdr in (_hdr(None), _hdr(attacker_jwt), _hdr(victim_jwt)):
        r = op(VICTIM, hdr)
        assert _ok(r.status_code, route, VICTIM), (route, r.status_code, r.text)
    r = op(ANON, _hdr(None))
    assert _ok(r.status_code, route, ANON)

    snap = security.stats.snapshot()
    assert snap["violations"] == {"missing_token": 1, "wrong_sub": 1}
    assert snap["blocked"] == 0
    assert snap["violations_by_route"] == {route: 2}


def test_report_mode_logs_structured_warning_without_plain_phone(client, monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "report")
    with caplog.at_level("WARNING", logger="lionpick.security"):
        assert client.get("/preferences", params={"user_id": VICTIM}).status_code == 200
    msgs = [r.getMessage() for r in caplog.records if "auth_enforce_violation" in r.getMessage()]
    assert len(msgs) == 1
    assert '"reason": "missing_token"' in msgs[0] and '"action": "allowed"' in msgs[0]
    assert "13800138000" not in msgs[0]   # 日志里不写明文手机号


def test_off_mode_skips_everything(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "off")
    assert client.get("/preferences", params={"user_id": VICTIM}).status_code == 200
    assert security.stats.snapshot()["checked"] == 0


def test_invalid_mode_value_falls_back_to_report(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "ENFORCE-typo")
    assert security.enforce_mode() == "report"
    assert client.get("/preferences", params={"user_id": VICTIM}).status_code == 200


def test_other_account_namespaces_are_protected(client, monkeypatch):
    """apple: / pw: / wechat: / 邮箱样式 都算账号 id;UUID 不算。"""
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "enforce")
    for uid in ("apple:000123.abc", "pw:alice@example.com", "wechat:demo", "alice@example.com", "future:ns-x"):
        assert security.is_account_id(uid)
        assert client.get("/preferences", params={"user_id": uid}).status_code == 401, uid
        assert client.get("/preferences", params={"user_id": uid},
                          headers=_hdr(jwt_session.issue(uid))).status_code == 200, uid
    assert not security.is_account_id(ANON)
    assert not security.is_account_id("dev-test-client-01")


def test_migrate_cannot_pull_from_someone_elses_account(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "enforce")
    r = client.post("/auth/migrate", json={"from_user_id": VICTIM, "to_user_id": ATTACKER},
                    headers=_hdr(jwt_session.issue(ATTACKER)))
    assert r.status_code == 403


def test_groupbuy_detail_masks_member_ids(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "enforce")
    pid = _product_id()
    g = client.post("/groupbuy/create", json={"user_id": VICTIM, "product_id": pid},
                    headers=_hdr(jwt_session.issue(VICTIM))).json()
    detail = client.get(f"/groupbuy/{g['group_id']}").json()   # 无需身份
    ids = [m["user_id"] for m in detail["members"] if m["kind"] != "simulated"]
    assert ids and all(i.startswith("u_") for i in ids)
    assert VICTIM not in str(detail)


# ---------------------------------------------------------------------------
# 失效保护:enforce + 源码默认密钥 → 一律不信任 token
# ---------------------------------------------------------------------------


def test_enforce_with_default_secret_rejects_even_valid_tokens(client, monkeypatch):
    monkeypatch.setattr(jwt_session, "_SECRET", jwt_session._DEFAULT_SECRET.encode())
    assert jwt_session.using_default_secret()
    forged = jwt_session.issue(VICTIM)   # 公开密钥 → 任何人都能签出这个 token

    monkeypatch.setenv("AUTH_ENFORCE_MODE", "enforce")
    assert not security.tokens_trustworthy()
    assert client.get("/preferences", params={"user_id": VICTIM}, headers=_hdr(forged)).status_code == 401
    assert security.stats.snapshot()["violations"] == {"untrusted_default_secret": 1}
    # /auth/delete 也走同一入口,同样拒绝。
    assert client.post("/auth/delete", json={"user_id": VICTIM}, headers=_hdr(forged)).status_code == 401
    assert client.post("/auth/verify", json={"jwt": forged}).status_code == 401
    # 匿名 id 不受影响。
    assert client.get("/preferences", params={"user_id": ANON}).status_code == 200

    # report 模式下保持旧行为(token 照常校验),只是告警。
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "report")
    assert security.tokens_trustworthy()
    assert client.post("/auth/verify", json={"jwt": forged}).status_code == 200


def test_enforce_with_real_secret_accepts_owner(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCE_MODE", "enforce")
    assert not jwt_session.using_default_secret()
    assert security.tokens_trustworthy()
    assert client.get("/preferences", params={"user_id": VICTIM},
                      headers=_hdr(jwt_session.issue(VICTIM))).status_code == 200
