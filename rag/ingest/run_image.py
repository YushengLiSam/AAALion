"""End-to-end image-index build: walk data/seed/*/images/, embed each
with OpenCLIP, upsert into Chroma `products_image` collection.

Usage: ``python -m rag.ingest.run_image``
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rag.ingest.embed_image import iter_product_images, embed_image_file  # noqa: E402


def _product_metadata(seed: Path, product_id: str) -> dict:
    # Find the product JSON for metadata; tolerate missing.
    for json_path in seed.glob("*/data/*.json"):
        if json_path.stem == product_id:
            try:
                p = json.loads(json_path.read_text(encoding="utf-8"))
                return {
                    "product_id": product_id,
                    "category": p.get("category", ""),
                    "sub_category": p.get("sub_category", ""),
                    "brand": p.get("brand", ""),
                    "base_price": p.get("base_price", 0),
                }
            except Exception:
                pass
    return {"product_id": product_id}


def main() -> int:
    seed = REPO_ROOT / "data" / "seed"
    if not seed.exists():
        print(f"seed not found: {seed}", file=sys.stderr)
        return 1

    import chromadb
    chroma_dir = REPO_ROOT / "data" / ".chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    col = client.get_or_create_collection(
        "products_image",
        metadata={"hnsw:space": "cosine"},
    )

    pairs = list(iter_product_images(seed))
    print(f"[clip] embedding {len(pairs)} product images")

    ids, embeddings, metadatas = [], [], []
    for i, (pid, path) in enumerate(pairs, 1):
        vec = embed_image_file(path)
        ids.append(pid)
        embeddings.append(vec)
        metadatas.append(_product_metadata(seed, pid))
        if i % 20 == 0 or i == len(pairs):
            print(f"  {i}/{len(pairs)}  {pid}")

    if ids:
        col.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)
    print(f"[clip] upserted; collection now has {col.count()} vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
