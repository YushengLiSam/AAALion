"""Backend's view of the RAG layer. Delegates to ``rag.retrieve.query``
when the vector index is available; falls back to keyword overlap if the
index is empty or unreachable."""

from __future__ import annotations

import sys
from pathlib import Path

# server/app/services/rag_client.py → parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def top_k(text: str, k: int = 3) -> list[dict]:
    try:
        from rag.retrieve.query import query
        hits = query(text, k=k)
        return [h.product for h in hits]
    except Exception:
        from rag.retrieve.query import _keyword_fallback  # type: ignore
        return [h.product for h in _keyword_fallback(text, k=k)]


def top_k_image(image_bytes: bytes, k: int = 3) -> list[dict]:
    """Visually similar top-k via CLIP. Empty list if CLIP isn't wired
    on this host or the image index is empty."""
    try:
        from rag.retrieve.query import query_image
        hits = query_image(image_bytes, k=k)
        return [h.product for h in hits]
    except Exception:
        return []


stub_top_k = top_k
