"""In-memory LRU cache for chat responses.

Keyed on hash(system_prompt + json(messages) + image_sha256). Value is the
full list of SSE event dicts the route emitted last time. On replay, the
route streams them with a small per-token delay so the UX still feels live.

This drops cost during demos (judges asking the same query twice) and is
the 4.4 ⭐ "热门查询缓存" rubric deliverable.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any


_MAX_ENTRIES = 200
_TTL_SECONDS = 600  # 10 min


class _Cache:
    def __init__(self) -> None:
        self._d: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        # Counters for /cache/stats — observable evidence of 4.4 ⭐ cache
        # hit rate at demo / defense time.
        self._hits: int = 0
        self._misses: int = 0
        self._expired: int = 0       # counted in misses, but tracked separately
        self._evictions: int = 0
        self._started_at: float = time.time()

    def get(self, key: str) -> list[dict] | None:
        item = self._d.get(key)
        if item is None:
            self._misses += 1
            return None
        ts, events = item
        if time.time() - ts > _TTL_SECONDS:
            self._d.pop(key, None)
            self._misses += 1
            self._expired += 1
            return None
        # LRU bump
        self._d.move_to_end(key)
        self._hits += 1
        return events

    def put(self, key: str, events: list[dict]) -> None:
        self._d[key] = (time.time(), events)
        self._d.move_to_end(key)
        while len(self._d) > _MAX_ENTRIES:
            self._d.popitem(last=False)
            self._evictions += 1

    def __len__(self) -> int:
        return len(self._d)

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._d),
            "max_size": _MAX_ENTRIES,
            "ttl_sec": _TTL_SECONDS,
            "hits": self._hits,
            "misses": self._misses,
            "expired_misses": self._expired,
            "evictions": self._evictions,
            "total_requests": total,
            "hit_rate": (self._hits / total) if total else 0.0,
            "uptime_sec": round(time.time() - self._started_at, 1),
        }

    def reset_stats(self) -> None:
        """Wipe counters without clearing the cache (useful before a demo)."""
        self._hits = 0
        self._misses = 0
        self._expired = 0
        self._evictions = 0
        self._started_at = time.time()


cache = _Cache()


def make_key(*, system_prompt: str, messages_json: str, image_sha: str = "") -> str:
    payload = f"{system_prompt}\n---\n{messages_json}\n---\n{image_sha}"
    return hashlib.sha256(payload.encode()).hexdigest()


def hash_image_bytes(b: bytes | None) -> str:
    if not b:
        return ""
    return hashlib.sha256(b).hexdigest()[:16]
