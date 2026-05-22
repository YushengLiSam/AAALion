"""End-to-end ingest: chunk → embed → upsert into the vector store.

Usage: ``python -m rag.ingest.run`` (run from the repo root)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rag.ingest.chunk import all_chunks  # noqa: E402
from rag.ingest.embed_text import embed_chunks  # noqa: E402
from rag.store import Doc, upsert_text, collection_count  # noqa: E402


def main() -> int:
    seed = REPO_ROOT / "data" / "seed"
    if not seed.exists():
        print(f"seed data not found at {seed}", file=sys.stderr)
        return 1

    chunks = list(all_chunks(seed))
    print(f"chunks: {len(chunks)}")

    embedded = embed_chunks(chunks)
    print(f"embedded: {len(embedded)}")

    docs: list[Doc] = []
    vecs: list[list[float]] = []
    for i, (chunk, vec) in enumerate(embedded):
        doc_id = f"{chunk.product_id}::{chunk.chunk_type}::{i}"
        meta = {
            **{k: v for k, v in chunk.metadata.items() if v is not None},
            "chunk_type": chunk.chunk_type,
            "text": chunk.text,
        }
        docs.append(Doc(id=doc_id, text=chunk.text, metadata=meta))
        vecs.append(vec)

    upsert_text(docs, vecs)
    print(f"upserted; collection now has {collection_count()} docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
