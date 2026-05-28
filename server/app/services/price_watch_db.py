"""Price-watch persistence + alert computation (R9.A.4, proposal #7).

Mirror of the repurchase_db pattern: SQLite at ``data/price_watch.db``,
sync sqlite3 from stdlib, wrapped at the route layer with
asyncio.to_thread. Lets users tap "提醒我降价" on a product card; the
backend stores (user_id, product_id, target_price_cny). On every
``GET /price_watch/alerts`` poll we check the current catalog price
against each stored target and surface alerts whose current price ≤
the target.

Snooze semantics: once an alert is delivered, mark ``last_alerted_at``
so the same alert doesn't fire again within ``snooze_hours`` (default
24 h). Mirrors repurchase_db's snooze logic.

Catalog price source: the product JSON's ``base_price`` field, or its
``price_cny`` if pre-computed (foreign products carry both). We do NOT
re-fetch live FX here — that's the chat route's concern. The watch
table stores user-supplied target_price_cny; the comparison happens
against whatever price the catalog currently exposes. If a product's
price drops in the JSON (we'd update via re-ingest), watchers get
alerted.
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
DB_PATH = REPO_ROOT / "data" / "price_watch.db"

# Default snooze between alerts for the same (user, product). Same
# 24-hour cadence as repurchase to keep the open-screen banner sane.
DEFAULT_SNOOZE_HOURS = 24


# ---------------------------------------------------------------------------
# Connection management — same pattern as repurchase_db.py
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
    """Idempotent table+index init. Called from FastAPI lifespan."""
    conn = _connection()
    with _conn_lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS price_watches (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            TEXT    NOT NULL,
                product_id         TEXT    NOT NULL,
                target_price_cny   REAL    NOT NULL,
                created_at         INTEGER NOT NULL,
                last_alerted_at    INTEGER DEFAULT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pw_user
                ON price_watches (user_id);

            CREATE INDEX IF NOT EXISTS idx_pw_user_alerted
                ON price_watches (user_id, last_alerted_at);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_pw_user_product_unique
                ON price_watches (user_id, product_id);
            """
        )


def _reset_for_tests() -> None:
    """Drop all rows + close the connection."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.execute("DROP TABLE IF EXISTS price_watches")
                _conn.close()
            except Exception:
                pass
            _conn = None


# ---------------------------------------------------------------------------
# Product lookup (same catalog index as repurchase_db.py uses)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _product_index() -> dict[str, dict]:
    """Walk data/seed/*/data/*.json → {product_id: product_dict}.
    Cached for the process lifetime; catalog is read-only at runtime.
    """
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


def _current_price_cny(product: dict) -> float | None:
    """Best-effort current CNY price.

    Priority:
      1. ``price_cny`` (foreign products that pre-computed via
         currency.normalize_product_prices; not always present for fresh
         catalog data, hence the fallback below).
      2. ``base_price`` if the product is natively CNY (provenance.currency
         == 'CNY' or no provenance info).
      3. None if we can't safely give a CNY number (foreign product
         without a normalize pass).

    Watch alerts only fire when we have a concrete CNY number to compare
    against the user's target_price_cny.
    """
    price_cny = product.get("price_cny")
    if isinstance(price_cny, (int, float)):
        return float(price_cny)
    prov = product.get("provenance") or {}
    currency = (prov.get("currency") or "CNY").upper()
    if currency == "CNY":
        base = product.get("base_price")
        if isinstance(base, (int, float)):
            return float(base)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_watch(
    user_id: str,
    product_id: str,
    *,
    target_price_cny: float,
    now: int | None = None,
) -> dict[str, Any]:
    """Persist a price-watch row. Upserts on (user_id, product_id) so the
    user can tap the button twice with a different target — the latest
    target wins.

    Returns the persisted row as a dict with the new id + target_price_cny.
    Raises ValueError if target_price_cny is non-positive or the
    product_id is unknown (no row in the catalog).
    """
    if not isinstance(target_price_cny, (int, float)) or target_price_cny <= 0:
        raise ValueError(f"target_price_cny must be > 0, got {target_price_cny!r}")
    product = _product_index().get(product_id)
    if product is None:
        raise ValueError(f"product_id {product_id!r} not in catalog")
    ts = int(now or time.time())

    conn = _connection()
    with _conn_lock:
        # SQLite UPSERT: insert or replace target_price_cny on conflict.
        cur = conn.execute(
            """
            INSERT INTO price_watches (user_id, product_id, target_price_cny, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
              target_price_cny = excluded.target_price_cny,
              last_alerted_at = NULL,
              created_at = excluded.created_at
            RETURNING id, target_price_cny
            """,
            (user_id, product_id, float(target_price_cny), ts),
        )
        row = cur.fetchone()
    return {"id": row["id"], "target_price_cny": float(row["target_price_cny"])}


def compute_due_alerts(
    user_id: str,
    *,
    now: int | None = None,
    limit: int | None = None,
    snooze_hours: int = DEFAULT_SNOOZE_HOURS,
) -> list[dict[str, Any]]:
    """Return all due alerts for a user as a list of dicts shaped like:

        {
          "watch_id": int,
          "product": dict (full catalog row),
          "current_price_cny": float,
          "target_price_cny": float,
          "savings_cny": float,
          "created_at": int,
        }

    A watch is "due" when:
      - the product still exists in the catalog,
      - we have a concrete current CNY price,
      - current_price_cny ≤ target_price_cny, AND
      - last_alerted_at is either NULL or older than `snooze_hours`.

    Side effect: matched alerts get their last_alerted_at set to `now`
    (so repeat polls within the snooze window don't re-deliver).
    """
    ts = int(now or time.time())
    snooze_cutoff = ts - max(0, int(snooze_hours)) * 3600
    conn = _connection()
    with _conn_lock:
        rows = conn.execute(
            """
            SELECT id, product_id, target_price_cny, created_at, last_alerted_at
              FROM price_watches
             WHERE user_id = ?
               AND (last_alerted_at IS NULL OR last_alerted_at < ?)
            """,
            (user_id, snooze_cutoff),
        ).fetchall()

    out: list[dict[str, Any]] = []
    index = _product_index()
    matched_ids: list[int] = []
    for row in rows:
        product = index.get(row["product_id"])
        if product is None:
            continue
        cur_price = _current_price_cny(product)
        target = float(row["target_price_cny"])
        if cur_price is None or cur_price > target:
            continue
        out.append({
            "watch_id": row["id"],
            "product": product,
            "current_price_cny": cur_price,
            "target_price_cny": target,
            "savings_cny": round(target - cur_price, 2),
            "created_at": row["created_at"],
        })
        matched_ids.append(row["id"])
        if limit is not None and len(out) >= limit:
            break

    # Mark-shown: bump last_alerted_at so we don't re-deliver within snooze.
    if matched_ids:
        conn = _connection()
        placeholders = ",".join("?" for _ in matched_ids)
        with _conn_lock:
            conn.execute(
                f"UPDATE price_watches SET last_alerted_at = ? WHERE id IN ({placeholders})",
                (ts, *matched_ids),
            )
    return out


def list_user_watches(user_id: str) -> list[dict[str, Any]]:
    """Return all watches a user has set (for a possible "manage watches"
    view in iOS later). Not used by the alert flow itself.
    """
    conn = _connection()
    with _conn_lock:
        rows = conn.execute(
            """
            SELECT id, product_id, target_price_cny, created_at, last_alerted_at
              FROM price_watches
             WHERE user_id = ?
             ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def remove_watch(user_id: str, product_id: str) -> bool:
    """Stop watching a specific product. True iff a row was removed."""
    conn = _connection()
    with _conn_lock:
        cur = conn.execute(
            "DELETE FROM price_watches WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
    return cur.rowcount > 0
