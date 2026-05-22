"""Embed text chunks. Default backend is `sentence-transformers` with a
Chinese-friendly model — free, runs on CPU, no network at inference time
once the model is cached. If a Doubao embedding endpoint becomes
available later, swap the `_encode` function.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable

from .chunk import Chunk

_MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_MODEL_NAME)


def embed_chunks(chunks: Iterable[Chunk]) -> list[tuple[Chunk, list[float]]]:
    chunks = list(chunks)
    if not chunks:
        return []
    model = _model()
    vectors = model.encode(
        [c.text for c in chunks],
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=16,
    )
    return list(zip(chunks, [list(map(float, v)) for v in vectors]))


def embed_query(text: str) -> list[float]:
    model = _model()
    vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return list(map(float, vec))
