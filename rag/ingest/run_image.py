"""End-to-end image-index build: walk data/seed/*/images/, embed each
with OpenCLIP, upsert into the ``products_image`` collection of the current
vector store (``RAG_STORE``: chroma by default, or milvus).

Usage: ``python -m rag.ingest.run_image [--rebuild]``

R15: previously this script bypassed ``rag.store`` and talked to chromadb
directly, so the image index could not follow a backend switch. It now goes
through the store API like the text ingest does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rag.ingest.chunk import _meta as _chunk_meta  # noqa: E402
from rag.ingest.embed_image import iter_product_images, embed_image_file  # noqa: E402
from rag.store import IMAGE_COLLECTION, get_store, write_isolation_required  # noqa: E402
from rag.store.load import ARTIFACT_DIR, save_artifact  # noqa: E402


def _product_metadata(seed: Path, product_id: str) -> dict:
    # Find the product JSON for metadata; tolerate missing.
    for json_path in seed.glob("*/data/*.json"):
        if json_path.stem == product_id:
            try:
                p = json.loads(json_path.read_text(encoding="utf-8"))
                # 与文本索引共用同一套元数据(含 brand_country / currency),
                # 图片集合因此也能按同样的字段过滤。
                meta = {k: v for k, v in _chunk_meta(p).items() if v is not None}
                meta["product_id"] = product_id
                return meta
            except Exception:
                pass
    return {"product_id": product_id}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the CLIP image index")
    ap.add_argument("--rebuild", action="store_true", help="drop and recreate the image collection first")
    args = ap.parse_args(argv)

    seed = REPO_ROOT / "data" / "seed"
    if not seed.exists():
        print(f"seed not found: {seed}", file=sys.stderr)
        return 1

    store = get_store()
    isolate = write_isolation_required()
    print(f"[clip] store: {store.backend}" + ("  (writes go through a torch-free subprocess)" if isolate else ""))

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
        artifact = save_artifact(ARTIFACT_DIR / f"{IMAGE_COLLECTION}.npz", ids, embeddings, metadatas)
        print(f"[clip] saved embeddings artifact: {artifact.relative_to(REPO_ROOT)}")
        if isolate:
            cmd = [sys.executable, "-m", "rag.store.load", str(artifact), "--collection", "image"]
            if args.rebuild:
                cmd.append("--rebuild")
            return subprocess.call(cmd, cwd=str(REPO_ROOT))
        # --rebuild 放在向量算完、存好之后才删旧集合:前面任何一步失败,线上索引都原封不动
        if args.rebuild:
            store.reset_collection(IMAGE_COLLECTION)
            print(f"[clip] reset collection {IMAGE_COLLECTION}")
        store.upsert_image(ids, embeddings, metadatas)
        if hasattr(store, "seal"):
            store.seal(IMAGE_COLLECTION)
    print(f"[clip] upserted; collection now has {store.image_count()} vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
