"""Embed text chunks with Doubao-embedding-vision and upsert into Qdrant.

This is a scaffold — Tujie will fill in the real embedding call once the
embedding model endpoint is confirmed (per the PDF, no API key needed for
the embedding model, just the same Doubao key).
"""

from __future__ import annotations

from typing import Iterable

from .chunk import Chunk


def embed_chunks(chunks: Iterable[Chunk]) -> list[tuple[Chunk, list[float]]]:
    """Embed text chunks → return (chunk, vector) pairs.

    TODO(tujie):
      - Call the Doubao embedding endpoint in batches of 16.
      - Handle the rate limit (RPM 700, TPM 800k).
      - Return one vector per chunk.

    For now this returns deterministic zero-vectors so downstream code
    can be wired and tested without network access.
    """
    return [(c, [0.0] * 768) for c in chunks]
