"""End-to-end ingest: chunk → embed → upsert into Qdrant.

Usage: ``python -m rag.ingest.run``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rag.ingest.chunk import all_chunks  # noqa: E402
from rag.ingest.embed_text import embed_chunks  # noqa: E402


def main() -> int:
    seed = REPO_ROOT / "data" / "seed"
    if not seed.exists():
        print(f"seed data not found at {seed}", file=sys.stderr)
        return 1

    chunks = list(all_chunks(seed))
    print(f"chunks: {len(chunks)}")

    embedded = embed_chunks(chunks)
    print(f"embedded: {len(embedded)}")

    # TODO(tujie): upsert into Qdrant. See rag/retrieve/query.py for the
    # collection name / payload schema.
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    print(f"would upsert into {qdrant_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
