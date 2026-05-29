"""Group-buy (拼单) simulation persistence (R9.B / proposal #11).

Mocks Pinduoduo's signature "邀 N 人拼单立省 X%" flow. A user opens a
group on a product; the group needs `target_size` members before the
discount unlocks, with a countdown. We CANNOT do a real social backend
(no friend graph, no push), so member growth toward the target is
**simulated deterministically from elapsed time**: every
`SIM_JOIN_INTERVAL_SEC` after creation, one "邻居" auto-joins, up to
target − 1 (the last seat is always left for a real human tap, so the
demo can show the user completing the group themselves).

Honesty: this is explicitly a UX/demo simulation. The fake joins are
labelled as such in the API payload (`member.kind == "simulated"`),
so we never claim real users joined.

Same sync-sqlite3 + lock pattern as the other R9 DBs. The simulated
count is DERIVED on read (no background thread) — clean and resumable
across restarts because it's a pure function of (created_at, now).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "data" / "group_buy.db"

# Discount the group unlocks once full. Indicative demo number.
GROUP_DISCOUNT_PCT = 15
# Default people needed (including the opener).
DEFAULT_TARGET_SIZE = 3
# Countdown window (24 h, Pinduoduo-style).
GROUP_TTL_SEC = 24 * 3600
# A simulated neighbour joins every N seconds (kept short so the demo
# visibly progresses while a judge watches).
SIM_JOIN_INTERVAL_SEC = 20


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
            CREATE TABLE IF NOT EXISTS groups (
                group_id     TEXT PRIMARY KEY,
                product_id   TEXT    NOT NULL,
                opener_id    TEXT    NOT NULL,
                target_size  INTEGER NOT NULL,
                created_at   INTEGER NOT NULL,
                expires_at   INTEGER NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'open'  -- open | complete | expired
            );

            CREATE TABLE IF NOT EXISTS group_members (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   TEXT    NOT NULL,
                user_id    TEXT    NOT NULL,
                kind       TEXT    NOT NULL,   -- 'opener' | 'real' | 'simulated'
                joined_at  INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_gm_group ON group_members (group_id);
            CREATE INDEX IF NOT EXISTS idx_g_opener ON groups (opener_id);
            """
        )


def _reset_for_tests() -> None:
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.execute("DROP TABLE IF EXISTS groups")
                _conn.execute("DROP TABLE IF EXISTS group_members")
                _conn.close()
            except Exception:
                pass
            _conn = None


@lru_cache(maxsize=1)
def _product_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    seed = REPO_ROOT / "data" / "seed"
    if not seed.is_dir():
        return out
    for path in seed.glob("*/data/*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pid = p.get("product_id")
        if isinstance(pid, str):
            out[pid] = p
    return out


def _gen_group_id(opener_id: str, product_id: str, ts: int) -> str:
    # Deterministic-ish short id; avoids importing uuid/random (Math.random
    # is fine in Python but we keep it simple + collision-safe enough for a
    # single-user demo).
    raw = f"{opener_id}:{product_id}:{ts}"
    return "g" + str(abs(hash(raw)) % (10 ** 10))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_group(
    opener_id: str,
    product_id: str,
    *,
    target_size: int = DEFAULT_TARGET_SIZE,
    now: int | None = None,
) -> dict[str, Any]:
    """Open a new group buy. The opener is auto-added as member #1.
    Raises ValueError on unknown product / bad target."""
    if product_id not in _product_index():
        raise ValueError(f"product_id {product_id!r} not in catalog")
    if target_size < 2 or target_size > 10:
        raise ValueError("target_size must be 2..10")
    ts = int(now or time.time())
    gid = _gen_group_id(opener_id, product_id, ts)
    conn = _connection()
    with _conn_lock:
        conn.execute(
            """INSERT INTO groups (group_id, product_id, opener_id, target_size,
                                   created_at, expires_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            (gid, product_id, opener_id, target_size, ts, ts + GROUP_TTL_SEC),
        )
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, kind, joined_at) VALUES (?, ?, 'opener', ?)",
            (gid, opener_id, ts),
        )
    return get_group(gid, now=ts)


def join_group(group_id: str, user_id: str, *, now: int | None = None) -> dict[str, Any]:
    """A real user joins (the 'I tapped the invite' path). Idempotent —
    re-joining is a no-op. Raises ValueError if the group is unknown."""
    ts = int(now or time.time())
    conn = _connection()
    with _conn_lock:
        g = conn.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if g is None:
            raise ValueError(f"group {group_id!r} not found")
        already = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        if not already:
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, kind, joined_at) VALUES (?, ?, 'real', ?)",
                (group_id, user_id, ts),
            )
    return get_group(group_id, now=ts)


def _simulated_join_count(created_at: int, now: int, target_size: int, real_count: int) -> int:
    """How many simulated neighbours have 'joined' by `now`. Caps so the
    total never exceeds target_size − 1 (leave the last seat for a real
    tap) and never goes negative."""
    elapsed = max(0, now - created_at)
    by_time = elapsed // SIM_JOIN_INTERVAL_SEC
    headroom = max(0, target_size - 1 - real_count)
    return int(min(by_time, headroom))


def get_group(group_id: str, *, now: int | None = None) -> dict[str, Any]:
    """Return the group's live state, including the time-derived simulated
    member count. Marks the group complete/expired as appropriate."""
    ts = int(now or time.time())
    conn = _connection()
    with _conn_lock:
        g = conn.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if g is None:
            raise ValueError(f"group {group_id!r} not found")
        members = conn.execute(
            "SELECT user_id, kind, joined_at FROM group_members WHERE group_id = ? ORDER BY joined_at",
            (group_id,),
        ).fetchall()

    real_count = len(members)
    sim_count = _simulated_join_count(g["created_at"], ts, g["target_size"], real_count)
    filled = real_count + sim_count
    expired = ts >= g["expires_at"]
    complete = filled >= g["target_size"]
    status = "complete" if complete else ("expired" if expired else "open")

    product = _product_index().get(g["product_id"], {})
    base_price = product.get("price_cny") or product.get("base_price")
    group_price = round(float(base_price) * (1 - GROUP_DISCOUNT_PCT / 100), 2) if base_price else None

    member_list = [
        {"user_id": m["user_id"], "kind": m["kind"], "joined_at": m["joined_at"]}
        for m in members
    ]
    # Render simulated neighbours as anonymized entries.
    for i in range(sim_count):
        member_list.append({"user_id": f"邻居{i+1}", "kind": "simulated", "joined_at": None})

    return {
        "group_id": group_id,
        "product_id": g["product_id"],
        "product": product or None,
        "target_size": g["target_size"],
        "filled": filled,
        "remaining": max(0, g["target_size"] - filled),
        "status": status,
        "discount_pct": GROUP_DISCOUNT_PCT,
        "group_price_cny": group_price,
        "expires_at": g["expires_at"],
        "seconds_left": max(0, g["expires_at"] - ts),
        "members": member_list,
    }


def list_active_for_user(user_id: str, *, now: int | None = None) -> list[dict[str, Any]]:
    """Groups this user opened (for an 'active group-buys' view)."""
    conn = _connection()
    with _conn_lock:
        rows = conn.execute(
            "SELECT group_id FROM groups WHERE opener_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [get_group(r["group_id"], now=now) for r in rows]
