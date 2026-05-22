"""Optional reranking pass for the top-k retrieval.

Two strategies for v2:
  - Cross-encoder reranker (e.g. BAAI/bge-reranker-base).
  - LLM-as-judge prompt sent to Doubao (slow but cheap).

For now this is identity — keeps the interface so the backend can call
it unconditionally.
"""

from __future__ import annotations

from typing import Sequence

from .query import Hit


def rerank(query_text: str, hits: Sequence[Hit], top_k: int | None = None) -> list[Hit]:
    out = list(hits)
    if top_k is not None:
        out = out[:top_k]
    return out
