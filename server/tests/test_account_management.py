"""R11 — account management tests for the local user store: change password,
forgot-password reset, account deletion, and admin list. Stdlib-only against a
temp SQLite DB (no FastAPI needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # server/
from app.services import user_store as us  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    us.DB_PATH = tmp_path / "users_test.db"
    us._conn = None
    us.init_schema()
    yield us.LocalUserStore()
    us._reset_for_tests()


def test_change_password_flow(store):
    u = store.register_password("a@b.com", "oldpass1", "A")
    with pytest.raises(ValueError):
        store.change_password(u["user_id"], "wrongold", "newpass1")
    store.change_password(u["user_id"], "oldpass1", "newpass1")
    with pytest.raises(ValueError):                      # old password no longer works
        store.verify_password("a@b.com", "oldpass1")
    assert store.verify_password("a@b.com", "newpass1")["user_id"] == u["user_id"]


def test_change_password_min_length(store):
    u = store.register_password("a@b.com", "oldpass1", None)
    with pytest.raises(ValueError):
        store.change_password(u["user_id"], "oldpass1", "short")  # < 6


def test_password_reset_flow(store):
    store.register_password("c@d.com", "pw123456", None)
    r = store.start_password_reset("c@d.com")
    assert r["sent"] and r.get("dev_code")
    wrong = "999999" if r["dev_code"] != "999999" else "000000"
    with pytest.raises(ValueError):
        store.verify_password_reset("c@d.com", wrong, "resetpw1")
    store.verify_password_reset("c@d.com", r["dev_code"], "resetpw1")
    assert store.verify_password("c@d.com", "resetpw1")["provider"] == "password"


def test_reset_nonexistent_account_does_not_leak(store):
    r = store.start_password_reset("nobody@x.com")
    assert r["sent"] is True and "dev_code" not in r     # no code for a non-account


def test_list_users_never_returns_secrets(store):
    store.register_password("e@f.com", "pw123456", "E")
    users = store.list_users()
    assert len(users) == 1
    assert all("pw_hash" not in x and "pw_salt" not in x for x in users)
    assert users[0]["has_password"] == 1
    assert users[0]["provider"] == "password"


def test_delete_requires_password(store):
    u = store.register_password("g@h.com", "pw123456", None)
    with pytest.raises(ValueError):
        store.delete_user(u["user_id"], "wrongpw", require_password=True)
    assert store.delete_user(u["user_id"], "pw123456", require_password=True)["deleted"]
    assert store.get_user(u["user_id"]) is None


def test_admin_delete_bypasses_password(store):
    u = store.register_password("i@j.com", "pw123456", None)
    assert store.delete_user(u["user_id"], None, require_password=False)["deleted"]
    assert store.get_user(u["user_id"]) is None
