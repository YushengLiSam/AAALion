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
from app.services.rag_client import retrieval_cache_stats

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
def stats() -> dict:
    """Snapshot of the cache counters since the last reset (or process start).

    Covers BOTH layers of the R10 caching stack:
      * the response cache (cache.py) — short-circuits the whole LLM stream;
      * the retrieval cache (rag_client) — memoizes the expensive hybrid +
        cross-encoder rerank, the dominant first-token cost on the CPU VM.

    Response-cache fields (flat, unchanged for back-compat with the iOS
    CacheStatsService):
      size / max_size / ttl_sec / hits / misses / expired_misses /
      evictions / total_requests / hit_rate / uptime_sec
    Retrieval-cache fields (prefixed, so no key collision):
      retrieval_cache_size / _max / _ttl_sec / _hits / _misses / _hit_rate
    """
    out = dict(cache.stats())
    out.update(retrieval_cache_stats())
    return out


@router.post("/reset")
def reset() -> dict:
    """Zero out counters without dropping cached entries. Returns the
    fresh-zero stats so the caller can confirm."""
    cache.reset_stats()
    return cache.stats()
