"""Unit + perf tests for the repurchase reminder feature.

Test matrix (REPURCHASE_PLAN §6):
  * empty user → []
  * single due item → 1 returned
  * 4 items, limit=3 → 3 returned in most-overdue-first order
  * snooze: same call twice within 24h, second is empty; 25h later, visible
  * perf: 1000 purchases, p95 < 50ms

Each test uses a fresh in-process DB at a temp path. The fixture monkey-patches
``DB_PATH`` + clears the cached connection so tests don't share state.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for p in (str(REPO_ROOT), str(SERVER_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Pick a product_id that actually exists in data/seed/ so record_purchase
# passes the catalog-lookup guard. We compute this once at import time.
def _pick_real_product_ids(n: int = 5) -> list[str]:
    from app.services import repurchase_db

    idx = repurchase_db._product_index()
    if not idx:
        pytest.skip("no products in data/seed; cannot test")
    # Stable ordering for reproducibility.
    return sorted(idx.keys())[:n]


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point the module at a tmp DB and init schema. Cleans up on exit."""
    from app.services import repurchase_db

    db_file = tmp_path / "repurchase_test.db"
    monkeypatch.setattr(repurchase_db, "DB_PATH", db_file)
    # Force the connection to be re-created against the new path.
    repurchase_db._reset_for_tests()
    repurchase_db.init_schema()
    yield repurchase_db
    repurchase_db._reset_for_tests()


# -----------------------------------------------------------------------------
# Phase 1 algorithm tests
# -----------------------------------------------------------------------------

def test_empty_user_returns_empty_list(fresh_db):
    """New user with no purchases → empty list (NOT an error)."""
    items = fresh_db.compute_due_items("brand-new-user-uuid")
    assert items == []


def test_single_due_item_returned(fresh_db):
    """One purchase past its cycle → that one item, days_overdue computed."""
    pid = _pick_real_product_ids(1)[0]
    now = int(time.time())
    fresh_db.record_purchase(
        "user1234",
        pid,
        purchased_at=now - 35 * 86400,
        cycle_days=30,
    )
    items = fresh_db.compute_due_items("user1234", now=now)
    assert len(items) == 1
    assert items[0]["product_id"] == pid
    assert items[0]["days_overdue"] == 5
    assert pid in items[0]["reminder_text"] or items[0]["product"]["title"] in items[0]["reminder_text"]


def test_top3_urgency_order_with_limit(fresh_db):
    """4 due items + limit=3 → returns the 3 most overdue, descending urgency."""
    pids = _pick_real_product_ids(4)
    now = int(time.time())
    # Insert in non-overdue-order to prove we sort, not preserve insertion order.
    overdues = [5, 30, 2, 20]  # days overdue
    for pid, overdue_days in zip(pids, overdues):
        fresh_db.record_purchase(
            "u-multi",
            pid,
            purchased_at=now - (30 + overdue_days) * 86400,
            cycle_days=30,
        )
    items = fresh_db.compute_due_items("u-multi", now=now, limit=3)
    assert len(items) == 3
    # Expected order: 30, 20, 5 → indices [1], [3], [0]
    expected_pids = [pids[1], pids[3], pids[0]]
    assert [it["product_id"] for it in items] == expected_pids
    assert items[0]["days_overdue"] == 30
    assert items[1]["days_overdue"] == 20
    assert items[2]["days_overdue"] == 5


def test_snooze_within_24h(fresh_db):
    """Codex review #1: same query within 24h → empty second time. After 25h → visible again."""
    pid = _pick_real_product_ids(1)[0]
    now = int(time.time())
    fresh_db.record_purchase("u-snooze", pid, purchased_at=now - 40 * 86400, cycle_days=30)

    first = fresh_db.compute_due_items("u-snooze", now=now)
    assert len(first) == 1, "should see the due item on first call"

    second = fresh_db.compute_due_items("u-snooze", now=now + 60)
    assert second == [], "snoozed: same item should NOT reappear within 24h"

    third = fresh_db.compute_due_items("u-snooze", now=now + 25 * 3600)
    assert len(third) == 1, "after 25h the snooze window expires; item visible again"


def test_unknown_product_id_rejected(fresh_db):
    """record_purchase guards against catalog-drift / typo'd product ids."""
    with pytest.raises(ValueError, match="unknown product_id"):
        fresh_db.record_purchase("u-bad", "p_does_not_exist_anywhere")


def test_cycle_default_picked_by_category(fresh_db):
    """When client doesn't pass cycle_days, server picks one based on the
    product's category — proves DEFAULT_CYCLE_DAYS_BY_CATEGORY is wired."""
    idx = fresh_db._product_index()
    # Find one beauty product and one electronics product for distinct cycles.
    beauty_pid = next(
        (pid for pid, p in idx.items() if p.get("category") == "美妆护肤"), None
    )
    electronics_pid = next(
        (pid for pid, p in idx.items() if p.get("category") == "数码电子"), None
    )
    if not beauty_pid or not electronics_pid:
        pytest.skip("catalog missing required categories for this test")

    now = int(time.time())
    r1 = fresh_db.record_purchase("u-cycle", beauty_pid, purchased_at=now)
    r2 = fresh_db.record_purchase("u-cycle", electronics_pid, purchased_at=now)
    # Beauty cycle = 60 days, electronics = 365 days (from constant table).
    assert r1["next_due_at"] == now + 60 * 86400
    assert r2["next_due_at"] == now + 365 * 86400


# -----------------------------------------------------------------------------
# Performance verification (Codex review #4)
# -----------------------------------------------------------------------------

def test_perf_p95_under_50ms_at_1000_purchases(fresh_db):
    """1000-row fixture, query p95 must be < 50ms thanks to idx_user_due."""
    pids = _pick_real_product_ids(5)
    now = int(time.time())
    # 100 users × 10 purchases each = 1000 rows. The query user has 10 of them.
    for u in range(100):
        for j in range(10):
            fresh_db.record_purchase(
                f"perf-u{u:03d}",
                pids[j % len(pids)],
                purchased_at=now - (j * 10 + 1) * 86400,
                cycle_days=30,
            )

    target_user = "perf-u042"
    latencies = []
    for _ in range(50):
        # Reset snooze to make every call fresh — we're benchmarking the SELECT.
        fresh_db._connection().execute(
            "UPDATE purchases SET last_shown_at = NULL WHERE user_id = ?",
            (target_user,),
        )
        t0 = time.perf_counter()
        fresh_db.compute_due_items(target_user, now=now, limit=3)
        latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    assert p95 < 50.0, (
        f"p95={p95:.1f}ms exceeds 50ms threshold — idx_user_due index may not be in use"
    )
