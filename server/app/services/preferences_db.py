"""Closed-loop preference store (R9.B / proposal #12).

When the user taps 👍 / 👎 on a product, we nudge a per-(brand / category /
sub_category) score for that anonymous device. Later retrievals consult
these scores and gently re-weight candidates — products from a
thumbed-up brand float up, thumbed-down ones sink. The model is just a
score table; no ML training, no cross-device tracking, no login.

Privacy posture (honest framing for defense):
  * Keyed ONLY by the iOS identifierForVendor (anonymous, per-install,
    resets on uninstall). No account, no email, no cross-device join.
  * One-tap wipe via DELETE /preferences.
  * The weights never leave our own backend; they're not sold/shared.
    (We say "anonymous per-device", NOT "runs entirely on your phone" —
    retrieval is server-side, so that would be untrue.)

Scoring:
  * 👍 like   → +1.0 to the product's brand, category, sub_category.
  * 👎 dislike→ −2.0 (loss-aversion: a reject is a stronger signal than
    a like — matches how people actually shop).
  * Score clamped to [−10, +10] so a few taps can't nuke a whole
    category permanently.

Same sync-sqlite3 + module-connection-with-lock pattern as
repurchase_db.py / price_watch_db.py. Callers in async routes wrap with
asyncio.to_thread.
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
DB_PATH = REPO_ROOT / "data" / "preferences.db"

LIKE_DELTA = 1.0
DISLIKE_DELTA = -2.0
SCORE_MIN = -10.0
SCORE_MAX = 10.0

# Dimensions we keep a score for. Order matters only for display.
_DIMENSIONS = ("brand", "category", "sub_category")


# ---------------------------------------------------------------------------
# Connection management — identical pattern to repurchase_db / price_watch_db
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None
_conn_lock = threading.Lock()


def _connection() -> sqlite3.Connection:
    global _conn
    with _conn_lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(
                str(DB_PATH),
                check_same_thread=False,
                isolation_level=None,
            )
            _conn.execute("PRAGMA journal_mode = WAL")
            _conn.execute("PRAGMA synchronous = NORMAL")
            _conn.row_factory = sqlite3.Row
        return _conn


def init_schema() -> None:
    """Idempotent table + index init. Called from FastAPI lifespan."""
    conn = _connection()
    with _conn_lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                dimension   TEXT    NOT NULL,   -- 'brand' | 'category' | 'sub_category'
                value       TEXT    NOT NULL,   -- e.g. 'Apple' / '美妆护肤' / '洁面'
                score       REAL    NOT NULL DEFAULT 0,
                updated_at  INTEGER NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_pref_unique
                ON preferences (user_id, dimension, value);

            CREATE INDEX IF NOT EXISTS idx_pref_user
                ON preferences (user_id);
            """
        )


def _reset_for_tests() -> None:
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.execute("DROP TABLE IF EXISTS preferences")
                _conn.close()
            except Exception:
                pass
            _conn = None


# ---------------------------------------------------------------------------
# Catalog lookup (same as repurchase_db / price_watch_db)
# ---------------------------------------------------------------------------

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


def _dimensions_of(product: dict) -> dict[str, str]:
    """Pull the (brand, category, sub_category) values off a product dict,
    skipping any that are missing/empty."""
    out: dict[str, str] = {}
    brand = (product.get("brand") or "").strip()
    category = (product.get("category") or "").strip()
    sub = (product.get("sub_category") or "").strip()
    if brand:
        out["brand"] = brand
    if category:
        out["category"] = category
    if sub:
        out["sub_category"] = sub
    return out


def _clamp(score: float) -> float:
    return max(SCORE_MIN, min(SCORE_MAX, score))


# ---------------------------------------------------------------------------
# Public write API
# ---------------------------------------------------------------------------

def record_feedback(user_id: str, product_id: str, signal: int, *, now: int | None = None) -> dict[str, Any]:
    """Apply a 👍 (+1) or 👎 (−1) feedback for a product.

    `signal` is +1 (like) or −1 (dislike). We translate to LIKE_DELTA /
    DISLIKE_DELTA and add to each of the product's brand / category /
    sub_category scores (clamped).

    Returns {"updated": {dimension: new_score, ...}} or raises ValueError
    on unknown product / bad signal.
    """
    if signal not in (1, -1):
        raise ValueError(f"signal must be +1 or -1, got {signal!r}")
    product = _product_index().get(product_id)
    if product is None:
        raise ValueError(f"product_id {product_id!r} not in catalog")
    delta = LIKE_DELTA if signal == 1 else DISLIKE_DELTA
    ts = int(now or time.time())
    dims = _dimensions_of(product)

    updated: dict[str, dict[str, float]] = {}
    conn = _connection()
    with _conn_lock:
        for dimension, value in dims.items():
            row = conn.execute(
                "SELECT score FROM preferences WHERE user_id=? AND dimension=? AND value=?",
                (user_id, dimension, value),
            ).fetchone()
            current = float(row["score"]) if row else 0.0
            new_score = _clamp(current + delta)
            conn.execute(
                """
                INSERT INTO preferences (user_id, dimension, value, score, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, dimension, value) DO UPDATE SET
                  score = excluded.score,
                  updated_at = excluded.updated_at
                """,
                (user_id, dimension, value, new_score, ts),
            )
            updated.setdefault(dimension, {})[value] = new_score
    return {"updated": updated}


def reset_preferences(user_id: str) -> int:
    """Wipe all preferences for a user. Returns the row count removed."""
    conn = _connection()
    with _conn_lock:
        cur = conn.execute("DELETE FROM preferences WHERE user_id = ?", (user_id,))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------

def get_weights(user_id: str) -> dict[str, dict[str, float]]:
    """Return {dimension: {value: score}} for a user. Empty dict if none.

    This is the structure the retrieval prior consumes. Cheap enough to
    call per chat request (a user accumulates at most a few dozen rows).
    """
    conn = _connection()
    with _conn_lock:
        rows = conn.execute(
            "SELECT dimension, value, score FROM preferences WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        out.setdefault(row["dimension"], {})[row["value"]] = float(row["score"])
    return out


def list_preferences(user_id: str) -> list[dict[str, Any]]:
    """Flat list for the iOS '我的偏好' management view, newest first."""
    conn = _connection()
    with _conn_lock:
        rows = conn.execute(
            """
            SELECT dimension, value, score, updated_at
              FROM preferences
             WHERE user_id = ?
             ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]
