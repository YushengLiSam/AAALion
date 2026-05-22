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

    def get(self, key: str) -> list[dict] | None:
        item = self._d.get(key)
        if item is None:
            return None
        ts, events = item
        if time.time() - ts > _TTL_SECONDS:
            self._d.pop(key, None)
            return None
        # LRU bump
        self._d.move_to_end(key)
        return events

    def put(self, key: str, events: list[dict]) -> None:
        self._d[key] = (time.time(), events)
        self._d.move_to_end(key)
        while len(self._d) > _MAX_ENTRIES:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


cache = _Cache()


def make_key(*, system_prompt: str, messages_json: str, image_sha: str = "") -> str:
    payload = f"{system_prompt}\n---\n{messages_json}\n---\n{image_sha}"
    return hashlib.sha256(payload.encode()).hexdigest()


def hash_image_bytes(b: bytes | None) -> str:
    if not b:
        return ""
    return hashlib.sha256(b).hexdigest()[:16]
