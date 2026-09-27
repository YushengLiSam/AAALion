"""把预先算好的向量文件灌进当前向量库后端。

    python -m rag.store.load data/.embeddings/products_text.npz --collection text --rebuild

向量文件(``.npz``)由入库脚本产出:ids、float32 向量矩阵、JSON 编码的 metadata、
可选的文本。把"算向量"和"写向量库"拆成两步有两个用处:

1. 向量只算一次,换后端 / 重建索引不用重算(百万级数据时这一步最贵);
2. 写入可以放进一个**不加载 torch** 的进程。macOS 上 torch 与 faiss-cpu 各带一份
   OpenMP 运行时,同进程写 Milvus Lite(后台用 faiss 建 HNSW)会让进程直接 abort,
   见 ``MilvusStore.write_needs_isolation``。

本模块刻意不 import torch / sentence-transformers。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPO_ROOT / "data" / ".embeddings"
_BATCH = 5000


def save_artifact(
    path: Path,
    ids: Sequence[str],
    vectors: Sequence[Sequence[float]],
    metadatas: Sequence[dict],
    texts: Sequence[str] | None = None,
    properties: dict | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        ids=np.asarray(list(ids), dtype=str),
        vectors=np.asarray(vectors, dtype=np.float32),
        metadatas=np.asarray([json.dumps(m, ensure_ascii=False) for m in metadatas], dtype=str),
        texts=np.asarray(list(texts) if texts is not None else [], dtype=str),
        properties=np.asarray(json.dumps(properties or {}, ensure_ascii=False), dtype=str),
    )
    return path


def load_artifact(path: Path) -> tuple[list[str], np.ndarray, list[dict], list[str] | None, dict]:
    """返回 (ids, 向量, metadata, 文本或 None, 索引级属性)。"""
    with np.load(path, allow_pickle=False) as z:
        ids = [str(x) for x in z["ids"]]
        vecs = np.asarray(z["vectors"], dtype=np.float32)
        metas = [json.loads(str(x)) for x in z["metadatas"]]
        texts = [str(x) for x in z["texts"]] if z["texts"].size else None
        props = json.loads(str(z["properties"])) if "properties" in z.files else {}
    if not (len(ids) == len(vecs) == len(metas)) or (texts is not None and len(texts) != len(ids)):
        raise ValueError(f"inconsistent artifact {path}: ids={len(ids)} vectors={len(vecs)} metas={len(metas)}")
    return ids, vecs, metas, texts, props


def main(argv: list[str] | None = None) -> int:
    from rag.store import IMAGE_COLLECTION, TEXT_COLLECTION, Doc, get_store

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artifact", type=Path)
    ap.add_argument("--collection", choices=("text", "image"), required=True)
    ap.add_argument("--rebuild", action="store_true", help="drop the target collection first")
    args = ap.parse_args(argv)

    if "torch" in sys.modules:  # 防御:这个进程的全部意义就是不带 torch
        print("warning: torch is loaded in the loader process", file=sys.stderr)

    ids, vecs, metas, texts, props = load_artifact(args.artifact)
    store = get_store()
    name = TEXT_COLLECTION if args.collection == "text" else IMAGE_COLLECTION
    if args.rebuild:
        store.reset_collection(name, properties=props)
    t0 = time.perf_counter()
    for i in range(0, len(ids), _BATCH):
        sl = slice(i, i + _BATCH)
        if args.collection == "text":
            docs = [
                Doc(id=d, text=(texts[j] if texts else str(m.get("text", ""))), metadata=m)
                for j, (d, m) in enumerate(zip(ids[sl], metas[sl]), start=i)
            ]
            store.upsert_text(docs, vecs[sl].tolist())
        else:
            store.upsert_image(ids[sl], vecs[sl].tolist(), metas[sl])
    if hasattr(store, "seal"):
        store.seal(name)  # 在这个不带 torch 的进程里把索引建完、落盘
    count = store.text_count() if args.collection == "text" else store.image_count()
    print(f"[load] {store.backend}: upserted {len(ids)} {args.collection} vectors in "
          f"{time.perf_counter() - t0:.1f}s; collection now has {count}")
    return 0 if count >= len(ids) or not args.rebuild else 4


if __name__ == "__main__":
    raise SystemExit(main())
