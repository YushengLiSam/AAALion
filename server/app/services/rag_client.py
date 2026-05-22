"""Wrapper around the rag/ package, for use from the backend.

Until Tujie's retrieve module lands, this returns a deterministic top-k
based on naive keyword overlap with the cached product index. This is
enough to unblock Sam and Shufeng.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.config import settings


@lru_cache(maxsize=1)
def _products() -> list[dict]:
    seed = settings.repo_root / "data" / "seed"
    out: list[dict] = []
    for json_path in seed.glob("*/data/*.json"):
        try:
            out.append(json.loads(json_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def stub_top_k(query: str, k: int = 3) -> list[dict]:
    """Stub retriever for parallel development. Replace with real call once
    rag/retrieve/query.py is wired up."""
    products = _products()
    if not query.strip():
        return products[:k]
    scored = [
        (sum(1 for token in query if token in p.get("title", "")), p)
        for p in products
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:k]]
