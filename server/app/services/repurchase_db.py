"""Repurchase reminder persistence + algorithm.

Single SQLite file at ``data/repurchase.db`` (gitignored, in-process,
zero new dependency — sqlite3 is stdlib). Mirrors the deployment model
we already use for Chroma: persistent, single-instance, simple.

Design see ``docs/REPURCHASE_PLAN.md`` for the full spec including
Codex review improvements (snooze, next_due_at index, IDFV caveat,
unit-test matrix).

This module is **sync**. Callers in async routes wrap with
``asyncio.to_thread`` — matches the rag_client / currency pattern.
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
DB_PATH = REPO_ROOT / "data" / "repurchase.db"


# Category → default repurchase cycle in days. Conservative baselines —
# real-world skincare lasts longer than people think; drinks turn over
# faster. These are FALLBACKS when a product's JSON doesn't carry an
# explicit `repurchase_cycle_days` field (see `_resolve_cycle_days`).
#
# Tuning rationale (R8.F.2):
#   美妆护肤 was 60d in v1; user feedback flagged that 10-day reminders
#   for 雅诗兰黛 75ml 精华 are absurd. Real high-end skincare lasts
#   3-4 months. Bumped category baseline to 90, with individual luxury
#   products allowed to override upward via the JSON field.
DEFAULT_CYCLE_DAYS_BY_CATEGORY: dict[str, int] = {
    "美妆护肤": 90,    # 多数面霜/精华 2-3 个月;高端 essence 用 JSON override 到 120
    "食品饮料": 14,    # 大多数饮品 / 牛奶 2 周;长保质期食品用 JSON override 到 30+
    "母婴健康": 30,    # 奶粉/尿不湿 1 个月
    "食品生活": 30,    # 日用品(洗衣液 / 卫生纸)1 个月
    "服饰运动": 180,   # 运动鞋 / 装备半年补一次
    "数码电子": 365,   # 电子产品很少复购,几乎不会触发提醒
    "家居家具": 365,
    "图书音像": 365,
}
DEFAULT_CYCLE_FALLBACK = 90  # 类目未知时的兜底


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

# Use a module-level connection guarded by a lock. SQLite supports concurrent
# readers, but ``sqlite3.connect(..., check_same_thread=False)`` lets the
# same handle be used across the FastAPI threadpool. We serialize writes
# with a lock to keep things simple and predictable.
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
                isolation_level=None,  # autocommit; we manage txns explicitly
            )
            _conn.execute("PRAGMA journal_mode = WAL")
            _conn.execute("PRAGMA synchronous = NORMAL")
            _conn.row_factory = sqlite3.Row
        return _conn


def init_schema() -> None:
    """Idempotent: create tables + indexes if not present. Call once at app
    startup (wired into ``server/app/main.py`` lifespan)."""
    conn = _connection()
    with _conn_lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT    NOT NULL,
                product_id      TEXT    NOT NULL,
                purchased_at    INTEGER NOT NULL,
                cycle_days      INTEGER NOT NULL,
                next_due_at     INTEGER NOT NULL,
                last_shown_at   INTEGER DEFAULT NULL,
                created_at      INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_user_due
                ON purchases (user_id, next_due_at);

            CREATE INDEX IF NOT EXISTS idx_user_shown
                ON purchases (user_id, last_shown_at);
            """
        )


def _reset_for_tests() -> None:
    """Drop all rows AND close the connection. Tests use this to start
    from a clean state. NOT for production use."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.execute("DROP TABLE IF EXISTS purchases")
                _conn.close()
            except Exception:
                pass
            _conn = None


# ---------------------------------------------------------------------------
# Product metadata lookup (lightweight — does NOT pull rag.retrieve which
# would import torch and friends)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _product_index() -> dict[str, dict]:
    """Walk data/seed/*/data/*.json → {product_id: product_dict}.

    Cached for the process lifetime since catalog is read-only at runtime.
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


def _product_metadata(product_id: str) -> dict | None:
    """Return product dict for a product_id, or None if unknown."""
    return _product_index().get(product_id)


def _resolve_cycle_days(product: dict) -> int:
    """Pick a cycle for a product, in preference order:

      1. ``product["repurchase_cycle_days"]`` — explicit per-product override
         in the catalog JSON. Most accurate. Use for products where the
         category default is obviously wrong (high-end 精华 lasts 120+;
         dish soap 30; mineral water 7).
      2. ``DEFAULT_CYCLE_DAYS_BY_CATEGORY[product.category]`` — category
         baseline. Covers the long tail without manual labelling.
      3. ``DEFAULT_CYCLE_FALLBACK`` — last-resort for unknown categories.

    Returns a positive int. Invalid / non-positive overrides are ignored.
    """
    override = product.get("repurchase_cycle_days")
    if isinstance(override, (int, float)) and int(override) > 0:
        return int(override)
    category = product.get("category") or ""
    return DEFAULT_CYCLE_DAYS_BY_CATEGORY.get(category, DEFAULT_CYCLE_FALLBACK)


# ---------------------------------------------------------------------------
# Public write API
# ---------------------------------------------------------------------------

def record_purchase(
    user_id: str,
    product_id: str,
    *,
    purchased_at: int | None = None,
    cycle_days: int | None = None,
) -> dict:
    """Persist a purchase and return the new row's id + next_due_at.

    Raises ``ValueError`` if ``product_id`` is not in the catalog —
    prevents poisoning the reminders table with phantom ids.

    Parameters
    ----------
    user_id        opaque client identifier (iOS identifierForVendor)
    product_id     must be present in ``data/seed/*/data/*.json``
    purchased_at   unix epoch seconds; defaults to now
    cycle_days     override default cycle; falls back to category default
    """
    if not user_id or not isinstance(user_id, str):
        raise ValueError("user_id must be a non-empty string")
    product = _product_metadata(product_id)
    if product is None:
        raise ValueError(f"unknown product_id: {product_id!r}")

    now = int(time.time())
    purchased_at = int(purchased_at) if purchased_at is not None else now
    cycle = int(cycle_days) if cycle_days is not None else _resolve_cycle_days(product)
    if cycle <= 0:
        raise ValueError("cycle_days must be positive")
    next_due_at = purchased_at + cycle * 86400

    conn = _connection()
    with _conn_lock:
        cur = conn.execute(
            """
            INSERT INTO purchases (user_id, product_id, purchased_at, cycle_days, next_due_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, product_id, purchased_at, cycle, next_due_at, now),
        )
        row_id = cur.lastrowid

    return {"id": row_id, "next_due_at": next_due_at}


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------

def _reminder_text(product: dict, days_overdue: int) -> str:
    """3-tier template (see REPURCHASE_PLAN §2.3). No LLM."""
    title = product.get("title") or "你之前买的商品"
    if days_overdue <= 0:
        return f"你购买的「{title}」大概该补货了,要再来一单吗?"
    if days_overdue <= 7:
        return f"你购买的「{title}」已经到周期了,要不要再来一单?"
    return f"你购买的「{title}」已经超期 {days_overdue} 天,该补货啦~"


def _product_for_response(product: dict) -> dict:
    """Pick the fields iOS needs for a product card. Matches the shape
    emitted by ``chat.py:_product_card_event`` so client UI is shared."""
    provenance = product.get("provenance") or {}
    return {
        "product_id": product.get("product_id"),
        "title": product.get("title"),
        "brand": product.get("brand"),
        "base_price": product.get("base_price"),
        "price_cny": product.get("price_cny"),
        "exchange_rate": product.get("exchange_rate"),
        "image_url": _image_url(product),
        "provenance": {
            "origin_country": provenance.get("origin_country", "CN"),
            "source_platform": provenance.get("source_platform", "AI-gen (demo)"),
            "currency": provenance.get("currency", "CNY"),
            "external_url": provenance.get("external_url"),
            "shipping_note": provenance.get("shipping_note"),
        },
    }


def _image_url(p: dict) -> str | None:
    """Mirror ``chat.py:_image_url``: local /static first, then external."""
    ip = p.get("image_path")
    if ip:
        return f"/static/{ip}"
    ext = p.get("image_url_external")
    if ext and isinstance(ext, str) and ext.startswith(("http://", "https://")):
        return ext
    return None


def compute_due_items(
    user_id: str,
    *,
    now: int | None = None,
    limit: int | None = None,
    snooze_hours: int = 24,
) -> list[dict]:
    """Return purchases due for reminder, ordered by urgency (most overdue
    first), with snooze applied. Marks returned rows as shown in the same
    transaction so the next call within ``snooze_hours`` won't repeat them.

    Codex review improvements all live here:
      * #1 snooze via ``last_shown_at`` cutoff + atomic mark-shown
      * #3 ``limit`` is a query param of ``/reminders``, no separate route
      * #4 query uses ``idx_user_due`` (next_due_at is a precomputed column)

    Returns empty list when user is new or has nothing due (high-frequency
    happy path — callers must NOT treat this as an error).
    """
    if not user_id or not isinstance(user_id, str):
        return []
    now = int(now) if now is not None else int(time.time())
    snooze_cutoff = now - snooze_hours * 3600

    base_sql = """
        SELECT id, product_id, purchased_at, cycle_days, next_due_at
        FROM purchases
        WHERE user_id = ?
          AND next_due_at <= ?
          AND (last_shown_at IS NULL OR last_shown_at < ?)
        ORDER BY next_due_at ASC
    """
    params: list[Any] = [user_id, now, snooze_cutoff]
    if limit is not None and limit > 0:
        base_sql += " LIMIT ?"
        params.append(int(limit))

    conn = _connection()
    with _conn_lock:
        conn.execute("BEGIN")
        try:
            rows = list(conn.execute(base_sql, params))
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE purchases SET last_shown_at = ? WHERE id IN ({placeholders})",
                    [now, *ids],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    out: list[dict] = []
    for r in rows:
        product = _product_metadata(r["product_id"])
        if product is None:
            # Catalog drifted out from under us — skip rather than crash.
            continue
        days_overdue = max(0, (now - r["next_due_at"]) // 86400)
        out.append(
            {
                "id": r["id"],
                "product_id": r["product_id"],
                "product": _product_for_response(product),
                "purchased_at": r["purchased_at"],
                "next_due_at": r["next_due_at"],
                "days_overdue": int(days_overdue),
                "reminder_text": _reminder_text(product, int(days_overdue)),
            }
        )
    return out
