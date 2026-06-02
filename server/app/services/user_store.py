"""User accounts + auth store, with a pluggable CLOUD SEAM (R10 / accounts).

狮选 supports two sign-in methods:
  * **Sign in with Apple** — verify Apple's identity JWT, key the user by
    the stable Apple `sub`.
  * **手机号 + 验证码 (phone + SMS code)** — what real Chinese apps use.

THE CLOUD SEAM
--------------
Yusheng (Sam) owns the cloud user service. This module defines the
`UserStore` Protocol that the rest of the backend depends on, plus a
factory `get_user_store()` chosen by env:

    USER_STORE_BACKEND = "local"   # (default) SQLite + dev SMS, this file
                       = "cloud"   # delegate to Sam's cloud service

`LocalUserStore` is the self-contained demo implementation (SQLite at
data/users.db; SMS codes are MOCKED — generated locally and returned in
the API response so the demo completes on one phone, never a real SMS).

`CloudUserStore` is a thin HTTP proxy to Sam's service. The exact HTTP
contract it expects is documented in
docs/cluely/ACCOUNTS_AND_REAL_GROUPBUY_PLAN.md §"Cloud seam contract" —
Sam implements those 5 endpoints and we flip USER_STORE_BACKEND=cloud +
set CLOUD_USER_API_BASE. Nothing else in the app changes.

Everything else in the backend (chat, group-buy, preferences, …) only
ever calls `get_user_store()`, so the local⇄cloud switch is invisible
to them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "users.db"

SMS_CODE_TTL_SEC = 300          # a code is valid 5 minutes
# Tables whose rows are keyed by user_id and should follow a user when
# they sign in (anonymous device id → account id). Used by migrate().
_REKEY_TABLES = (
    ("preferences", "preferences.db"),
    ("price_watches", "price_watch.db"),
    ("purchases", "repurchase.db"),
)


@runtime_checkable
class UserStore(Protocol):
    """The contract the app depends on. LocalUserStore (here) and
    CloudUserStore (Sam) both satisfy it."""

    def verify_apple(self, identity_token: str, display_name: str | None) -> dict[str, Any]: ...
    def start_phone(self, phone: str) -> dict[str, Any]: ...
    def verify_phone(self, phone: str, code: str) -> dict[str, Any]: ...
    # R10.bugfix — password auth (email/phone + password). Simpler than SMS
    # for the demo; identifier is an email (contains '@') or a phone (digits).
    def register_password(self, identifier: str, password: str, display_name: str | None) -> dict[str, Any]: ...
    def verify_password(self, identifier: str, password: str) -> dict[str, Any]: ...
    def get_user(self, user_id: str) -> dict[str, Any] | None: ...
    def migrate(self, from_user_id: str, to_user_id: str) -> dict[str, Any]: ...
    # R11 (demo) — mock WeChat login. NOT real WeChat OAuth (that needs
    # 企业资质 + 微信开放平台 SDK + review); returns a stable demo account so
    # the production SDK can swap in behind this same method later.
    def mock_wechat(self, display_name: str | None) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Local implementation (SQLite + mocked SMS) — the default / demo backend
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None
_conn_lock = threading.Lock()


def _connection() -> sqlite3.Connection:
    global _conn
    with _conn_lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
            _conn.execute("PRAGMA journal_mode = WAL")
            _conn.execute("PRAGMA synchronous = NORMAL")
            _conn.row_factory = sqlite3.Row
        return _conn


def init_schema() -> None:
    conn = _connection()
    with _conn_lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id      TEXT PRIMARY KEY,   -- 'apple:<sub>' | 'phone:<e164>' | 'pw:<identifier>'
                provider     TEXT NOT NULL,      -- 'apple' | 'phone' | 'password'
                display_name TEXT,
                created_at   INTEGER NOT NULL,
                pw_hash      TEXT,               -- hex(pbkdf2_hmac sha256); only for provider='password'
                pw_salt      TEXT                -- hex(salt 16 bytes)
            );
            CREATE TABLE IF NOT EXISTS sms_codes (
                phone      TEXT PRIMARY KEY,
                code       TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            """
        )
        # R10.bugfix — idempotent column adds for upgrade-in-place. If the
        # users table existed before pw_hash/pw_salt, this brings it forward
        # without dropping data.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "pw_hash" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN pw_hash TEXT")
        if "pw_salt" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN pw_salt TEXT")


def _reset_for_tests() -> None:
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.execute("DROP TABLE IF EXISTS users")
                _conn.execute("DROP TABLE IF EXISTS sms_codes")
                _conn.close()
            except Exception:
                pass
            _conn = None


def _gen_code() -> str:
    """6-digit numeric code. Uses os.urandom (Math.random is unavailable
    in this runtime and we want non-predictable codes anyway)."""
    n = int.from_bytes(os.urandom(4), "big") % 1_000_000
    return f"{n:06d}"


_PBKDF2_ITERS = 100_000  # stdlib pbkdf2_hmac; demo-tier security, no new deps.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_PHONE_RE_LOCAL = re.compile(r"^\+?\d{6,15}$")


def _normalize_identifier(identifier: str) -> str:
    """Email → lowercased; phone → digits-only with optional leading '+'.
    Returns "" if neither shape matches."""
    s = identifier.strip()
    if _EMAIL_RE.fullmatch(s):
        return s.lower()
    if _PHONE_RE_LOCAL.fullmatch(s):
        return s
    return ""


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Return (pw_hash_hex, salt_hex). salt is 16 random bytes if not supplied."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return digest.hex(), salt.hex()


def _decode_apple_sub(identity_token: str) -> tuple[str, str | None]:
    """Extract (sub, email) from an Apple identity JWT.

    ⚠️ DEMO-ONLY: this decodes the JWT payload WITHOUT verifying Apple's
    signature, because (a) it avoids a new crypto dependency and (b) this
    Mac's DNS to appleid.apple.com is flaky offline. THE CLOUD STORE MUST
    verify the signature against Apple's JWKS + check aud/iss/exp. Never
    ship this unverified path to production — it's a local demo seam only.
    """
    try:
        payload_b64 = identity_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad base64url
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("sub", ""), claims.get("email")
    except Exception:
        return "", None


class LocalUserStore:
    """SQLite-backed store with mocked SMS. The default demo backend."""

    def _upsert(self, user_id: str, provider: str, display_name: str | None) -> dict[str, Any]:
        ts = int(time.time())
        conn = _connection()
        with _conn_lock:
            existing = conn.execute("SELECT display_name FROM users WHERE user_id=?", (user_id,)).fetchone()
            if existing:
                if display_name and not existing["display_name"]:
                    conn.execute("UPDATE users SET display_name=? WHERE user_id=?", (display_name, user_id))
                    name = display_name
                else:
                    name = existing["display_name"]
            else:
                conn.execute(
                    "INSERT INTO users (user_id, provider, display_name, created_at) VALUES (?,?,?,?)",
                    (user_id, provider, display_name, ts),
                )
                name = display_name
        return {"user_id": user_id, "provider": provider, "display_name": name}

    def mock_wechat(self, display_name: str | None) -> dict[str, Any]:
        # R11 DEMO ONLY — not real WeChat OAuth. Returns one stable demo
        # WeChat account (`wechat:demo`) so favorites / preferences persist
        # across taps and the demo stays consistent.
        return self._upsert("wechat:demo", "wechat", display_name or "微信用户(演示)")

    def verify_apple(self, identity_token: str, display_name: str | None) -> dict[str, Any]:
        sub, _email = _decode_apple_sub(identity_token)
        if not sub:
            raise ValueError("invalid Apple identity token")
        return self._upsert(f"apple:{sub}", "apple", display_name)

    def start_phone(self, phone: str) -> dict[str, Any]:
        code = _gen_code()
        ts = int(time.time())
        conn = _connection()
        with _conn_lock:
            conn.execute(
                "INSERT INTO sms_codes (phone, code, expires_at) VALUES (?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET code=excluded.code, expires_at=excluded.expires_at",
                (phone, code, ts + SMS_CODE_TTL_SEC),
            )
        # DEMO: return the code so the flow completes without a real SMS.
        # The cloud store sends an SMS and returns {"sent": true} with NO
        # `dev_code` field.
        return {"sent": True, "dev_code": code, "demo": True}

    def verify_phone(self, phone: str, code: str) -> dict[str, Any]:
        ts = int(time.time())
        conn = _connection()
        with _conn_lock:
            row = conn.execute("SELECT code, expires_at FROM sms_codes WHERE phone=?", (phone,)).fetchone()
            if row is None or ts > row["expires_at"]:
                raise ValueError("验证码已过期或不存在 / code expired or not found")
            if code != row["code"]:
                raise ValueError("验证码错误 / wrong code")
            conn.execute("DELETE FROM sms_codes WHERE phone=?", (phone,))
        return self._upsert(f"phone:{phone}", "phone", None)

    def register_password(self, identifier: str, password: str, display_name: str | None) -> dict[str, Any]:
        ident = _normalize_identifier(identifier)
        if not ident:
            raise ValueError("identifier must be an email or phone number")
        if len(password) < 6:
            raise ValueError("密码至少 6 位 / password must be ≥ 6 chars")
        user_id = f"pw:{ident}"
        ts = int(time.time())
        pw_hash, pw_salt = _hash_password(password)
        conn = _connection()
        with _conn_lock:
            existing = conn.execute(
                "SELECT provider, pw_hash FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if existing is not None and existing["pw_hash"]:
                raise ValueError("账号已存在,请直接登录 / account already exists, please sign in")
            if existing is None:
                conn.execute(
                    "INSERT INTO users (user_id, provider, display_name, created_at, pw_hash, pw_salt) "
                    "VALUES (?,?,?,?,?,?)",
                    (user_id, "password", display_name, ts, pw_hash, pw_salt),
                )
            else:
                conn.execute(
                    "UPDATE users SET provider='password', display_name=COALESCE(?, display_name), "
                    "pw_hash=?, pw_salt=? WHERE user_id=?",
                    (display_name, pw_hash, pw_salt, user_id),
                )
        return {"user_id": user_id, "provider": "password", "display_name": display_name}

    def verify_password(self, identifier: str, password: str) -> dict[str, Any]:
        ident = _normalize_identifier(identifier)
        if not ident:
            raise ValueError("identifier must be an email or phone number")
        user_id = f"pw:{ident}"
        conn = _connection()
        with _conn_lock:
            row = conn.execute(
                "SELECT display_name, pw_hash, pw_salt FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
        if row is None or not row["pw_hash"] or not row["pw_salt"]:
            raise ValueError("账号或密码错误 / invalid account or password")
        candidate, _ = _hash_password(password, row["pw_salt"])
        # Constant-time compare to keep timing leaks off the table.
        if not hmac.compare_digest(candidate, row["pw_hash"]):
            raise ValueError("账号或密码错误 / invalid account or password")
        return {"user_id": user_id, "provider": "password", "display_name": row["display_name"]}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        conn = _connection()
        with _conn_lock:
            row = conn.execute(
                "SELECT user_id, provider, display_name, created_at FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def migrate(self, from_user_id: str, to_user_id: str) -> dict[str, Any]:
        """Re-key a device's anonymous rows (preferences / price-watch /
        repurchase) to the signed-in account, so nothing is lost on first
        sign-in. group_buy is intentionally NOT migrated (groups are tied
        to the session that opened them)."""
        if not from_user_id or not to_user_id or from_user_id == to_user_id:
            return {"migrated": {}}
        migrated: dict[str, int] = {}
        for table, db_file in _REKEY_TABLES:
            try:
                path = REPO_ROOT / "data" / db_file
                if not path.exists():
                    continue
                c = sqlite3.connect(str(path), isolation_level=None)
                cur = c.execute(
                    f"UPDATE {table} SET user_id=? WHERE user_id=?", (to_user_id, from_user_id)
                )
                migrated[table] = cur.rowcount
                c.close()
            except Exception:
                continue
        return {"migrated": migrated}


# ---------------------------------------------------------------------------
# Cloud implementation (thin HTTP proxy to Sam's service) — the seam
# ---------------------------------------------------------------------------

class CloudUserStore:
    """Delegates every call to Sam's cloud user service. Implements the
    SAME UserStore contract, so flipping USER_STORE_BACKEND=cloud is the
    only change the app needs. The cloud service must expose the 5
    endpoints documented in the accounts plan (§Cloud seam contract).

    Kept deliberately thin — Sam owns the real logic (verified Apple JWT,
    real SMS provider, durable user DB)."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")

    def _post(self, path: str, body: dict) -> dict[str, Any]:
        import httpx  # already a backend dependency (currency uses it)

        with httpx.Client(timeout=10) as client:
            r = client.post(f"{self.base}{path}", json=body)
            r.raise_for_status()
            return r.json()

    def _get(self, path: str, params: dict) -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=10) as client:
            r = client.get(f"{self.base}{path}", params=params)
            r.raise_for_status()
            return r.json()

    def verify_apple(self, identity_token: str, display_name: str | None) -> dict[str, Any]:
        return self._post("/users/apple", {"identity_token": identity_token, "display_name": display_name})

    def start_phone(self, phone: str) -> dict[str, Any]:
        return self._post("/users/phone/start", {"phone": phone})

    def verify_phone(self, phone: str, code: str) -> dict[str, Any]:
        return self._post("/users/phone/verify", {"phone": phone, "code": code})

    def register_password(self, identifier: str, password: str, display_name: str | None) -> dict[str, Any]:
        return self._post("/users/password/register", {"identifier": identifier, "password": password, "display_name": display_name})

    def verify_password(self, identifier: str, password: str) -> dict[str, Any]:
        return self._post("/users/password/login", {"identifier": identifier, "password": password})

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        try:
            return self._get("/users/get", {"user_id": user_id})
        except Exception:
            return None

    def migrate(self, from_user_id: str, to_user_id: str) -> dict[str, Any]:
        return self._post("/users/migrate", {"from_user_id": from_user_id, "to_user_id": to_user_id})

    def mock_wechat(self, display_name: str | None) -> dict[str, Any]:
        return self._post("/users/wechat", {"display_name": display_name})


# ---------------------------------------------------------------------------
# Factory — the single switch point
# ---------------------------------------------------------------------------

_store: UserStore | None = None


def get_user_store() -> UserStore:
    global _store
    if _store is None:
        backend = os.getenv("USER_STORE_BACKEND", "local").lower()
        if backend == "cloud":
            base = os.getenv("CLOUD_USER_API_BASE", "").strip()
            if base:
                _store = CloudUserStore(base)
            else:
                # Misconfigured cloud → fall back to local so the app still
                # boots; logged by the route layer on first use.
                _store = LocalUserStore()
        else:
            _store = LocalUserStore()
    return _store
