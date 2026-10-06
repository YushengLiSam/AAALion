"""End-to-end ingest: chunk → embed → upsert into the vector store.

Usage (run from the repo root)::

    python -m rag.ingest.run              # upsert into the current RAG_STORE backend
    python -m rag.ingest.run --rebuild    # drop + recreate the collection first

The backend is chosen by ``RAG_STORE`` (``chroma`` default, or ``milvus``);
see ``rag/store/__init__.py``. ``--rebuild`` is what stamps a fresh
schema version onto the index — an in-place upsert into an older index
keeps that index's (older) version, because its existing docs still lack
the newer fields.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from rag.ingest.chunk import all_chunks, iter_products  # noqa: E402
from rag.ingest.embed_text import embed_chunks  # noqa: E402
from rag.store import (  # noqa: E402
    TEXT_COLLECTION,
    Doc,
    collection_count,
    get_store,
    serving_collection,
    upsert_text,
    write_isolation_required,
)
from rag.retrieve.brand_origin import origin_fingerprint  # noqa: E402
from rag.store.load import ARTIFACT_DIR, save_artifact  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="drop and recreate the text collection before upserting")
    args = ap.parse_args(argv)

    seed = REPO_ROOT / "data" / "seed"
    if not seed.exists():
        print(f"seed data not found at {seed}", file=sys.stderr)
        return 1

    store = get_store()
    isolate = write_isolation_required()
    print(f"store: {store.backend}" + ("  (writes go through a torch-free subprocess)" if isolate else ""))
    # 索引级属性:产地解析指纹。查询侧只有指纹一致才把国别反选下推到数据库。
    properties = {"origin_fp": origin_fingerprint(iter_products(seed))}

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

    # 向量文件每次都存:换后端 / 重建索引时可直接 `python -m rag.store.load`,不用重算向量
    artifact = save_artifact(
        ARTIFACT_DIR / f"{TEXT_COLLECTION}.npz",
        [d.id for d in docs], vecs, [d.metadata for d in docs], [d.text for d in docs],
        properties=properties,
    )
    print(f"saved embeddings artifact: {artifact.relative_to(REPO_ROOT)}")

    if isolate:
        cmd = [sys.executable, "-m", "rag.store.load", str(artifact), "--collection", "text"]
        if args.rebuild:
            cmd.append("--rebuild")
        return subprocess.call(cmd, cwd=str(REPO_ROOT))

    # --rebuild 放在向量算完、存好之后才删旧集合:前面任何一步失败,线上索引都原封不动
    if args.rebuild:
        store.reset_collection(TEXT_COLLECTION, properties=properties)
        print(f"reset collection {TEXT_COLLECTION} (origin_fp={properties['origin_fp']})")
    upsert_text(docs, vecs)
    if hasattr(store, "seal"):
        store.seal(TEXT_COLLECTION)
    print(f"upserted; collection now has {collection_count()} docs")
    # 版本化 Milvus(RAG_MILVUS_VERSIONED=1)下,这里显示别名此刻指向的物理集合
    print(f"serving collection: {serving_collection(store, TEXT_COLLECTION)}")
    print(f"filterable fields: {sorted(store.filterable_fields(TEXT_COLLECTION))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
