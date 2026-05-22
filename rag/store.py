"""Vector store wrapper. Primary backend: Chroma in-process (no docker).
Qdrant remains supported but the dev default is Chroma because solo-dev
prefers fewer moving parts. Switch via ``RAG_STORE=qdrant``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
CHROMA_DIR = REPO_ROOT / "data" / ".chroma"
TEXT_COLLECTION = "products_text"
IMAGE_COLLECTION = "products_image"


@dataclass
class Doc:
    id: str
    text: str
    metadata: dict


@dataclass
class Hit:
    id: str
    score: float  # higher is better
    metadata: dict


def _chroma_client():
    import chromadb
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def upsert_text(docs: Sequence[Doc], embeddings: Sequence[Sequence[float]]) -> None:
    client = _chroma_client()
    col = client.get_or_create_collection(TEXT_COLLECTION, metadata={"hnsw:space": "cosine"})
    col.upsert(
        ids=[d.id for d in docs],
        documents=[d.text for d in docs],
        metadatas=[d.metadata for d in docs],
        embeddings=[list(e) for e in embeddings],
    )


def query_text(
    embedding: Sequence[float],
    k: int = 5,
    *,
    where: dict | None = None,
) -> list[Hit]:
    client = _chroma_client()
    col = client.get_or_create_collection(TEXT_COLLECTION, metadata={"hnsw:space": "cosine"})
    result = col.query(
        query_embeddings=[list(embedding)],
        n_results=k,
        where=where or None,
    )
    ids = (result.get("ids") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    hits: list[Hit] = []
    for i, m, d in zip(ids, metas, dists):
        hits.append(Hit(id=i, score=1.0 - float(d), metadata=m or {}))
    return hits


def collection_count() -> int:
    client = _chroma_client()
    col = client.get_or_create_collection(TEXT_COLLECTION)
    return col.count()
