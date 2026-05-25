"""Cache observability endpoints.

Exposes the in-memory LRU cache's hit/miss counters so we can demonstrate
the 4.4 ⭐ "热门查询缓存" rubric item with real numbers at defense time —
not just "we wrote the cache", but "the cache is X% hit rate on demo load,
which cut median first-delta latency from Yms (miss) to Zms (hit)".

Endpoints:
  GET  /cache/stats   — current counters + hit rate + uptime
  POST /cache/reset   — wipe counters (NOT the entries). Useful before
                        recording a clean demo run.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.services.cache import cache

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
def stats() -> dict:
    """Snapshot of the cache's counters since the last reset (or process start).

    Fields:
      size            — current number of entries
      max_size        — LRU capacity (oldest evicted when full)
      ttl_sec         — entry TTL; older entries miss + get evicted on read
      hits / misses   — counts of get() that returned vs returned None
      expired_misses  — subset of misses caused by TTL expiry
      evictions       — entries removed due to LRU pressure
      total_requests  — hits + misses
      hit_rate        — hits / total_requests (0.0 when no traffic yet)
      uptime_sec      — seconds since counters started
    """
    return cache.stats()


@router.post("/reset")
def reset() -> dict:
    """Zero out counters without dropping cached entries. Returns the
    fresh-zero stats so the caller can confirm."""
    cache.reset_stats()
    return cache.stats()
