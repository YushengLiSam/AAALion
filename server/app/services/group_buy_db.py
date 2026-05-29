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
    """A real user joins (the '我也来拼 / I tapped the invite' path).
    Idempotent for the same user_id. In this demo, a real join also
    materializes any remaining seats with simulated neighbours so the
    group completes immediately — the satisfying "拼单成功" moment.
    Raises ValueError if the group is unknown."""
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
        # Fill any leftover seats with sticky simulated neighbours so the
        # tap visibly completes the group.
        _ensure_simulated(conn, g, ts, fill_to_complete=True)
    return get_group(group_id, now=ts)


def _ensure_simulated(conn: sqlite3.Connection, g: sqlite3.Row, now: int, *, fill_to_complete: bool = False) -> None:
    """Persist simulated neighbours as STICKY rows (additive-only — never
    removed). Without `fill_to_complete`, neighbours trickle in over time
    (1 per SIM_JOIN_INTERVAL_SEC) up to target_size; with it, all
    remaining seats fill at once (used by the 我也来拼 tap).

    Sticky persistence is the fix for the 'tap join → neighbour vanishes'
    bug: because the simulated members are real rows, a subsequent real
    join can't shrink them back out via dynamic recomputation.
    Assumes the caller holds `_conn_lock`."""
    gid = g["group_id"]
    target = g["target_size"]
    rows = conn.execute(
        "SELECT kind FROM group_members WHERE group_id = ?", (gid,)
    ).fetchall()
    total = len(rows)
    cur_sim = sum(1 for r in rows if r["kind"] == "simulated")
    if total >= target:
        return
    if fill_to_complete:
        desired_total = target
    else:
        elapsed = max(0, now - g["created_at"])
        # Non-sim seats already taken (opener + real joins).
        non_sim = total - cur_sim
        by_time = non_sim + (elapsed // SIM_JOIN_INTERVAL_SEC)
        desired_total = int(min(target, by_time))
    to_add = max(0, desired_total - total)
    for i in range(to_add):
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, kind, joined_at) VALUES (?, ?, 'simulated', ?)",
            (gid, f"邻居{cur_sim + i + 1}", now),
        )


def get_group(group_id: str, *, now: int | None = None) -> dict[str, Any]:
    """Return the group's live state. Time-based simulated neighbours are
    persisted (sticky) on read so progress is monotonic. Marks the group
    complete/expired as appropriate."""
    ts = int(now or time.time())
    conn = _connection()
    with _conn_lock:
        g = conn.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,)).fetchone()
        if g is None:
            raise ValueError(f"group {group_id!r} not found")
        # Materialize any neighbours due by now (additive, sticky).
        _ensure_simulated(conn, g, ts, fill_to_complete=False)
        members = conn.execute(
            "SELECT user_id, kind, joined_at FROM group_members WHERE group_id = ? ORDER BY joined_at, id",
            (group_id,),
        ).fetchall()

    filled = len(members)
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
