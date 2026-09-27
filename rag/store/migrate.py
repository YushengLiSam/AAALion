"""跨后端迁移:把一个后端的索引原样复制到另一个后端,不重新计算向量。

    python -m rag.store.migrate --from chroma --to milvus --rebuild
    python -m rag.store.migrate --from chroma --to milvus --collections text

向量逐位相同,两个后端之间的检索结果才可以严格比对(见 ``rag.eval.store_parity``)。

源索引必须是 schema v2(带 ``brand_country`` / ``currency``)。旧索引搬过去,
目标库里这两列会全是空值,而 Milvus 的显式 schema 会让查询侧以为"字段齐全"。
这种情况默认拒绝,需要显式加 ``--allow-legacy``。
"""

from __future__ import annotations

import argparse
import sys
import time

from rag.store import (
    IMAGE_COLLECTION,
    SUPPORTED_BACKENDS,
    TEXT_COLLECTION,
    Doc,
    get_store,
)

_COLLECTIONS = {"text": TEXT_COLLECTION, "image": IMAGE_COLLECTION}
_REQUIRED_V2 = ("brand_country", "currency")


def migrate(src_name: str, dst_name: str, collections: list[str], *, rebuild: bool, allow_legacy: bool, batch: int) -> int:
    if src_name == dst_name:
        print("source and target backend are the same", file=sys.stderr)
        return 2
    src, dst = get_store(src_name), get_store(dst_name)

    for label in collections:
        name = _COLLECTIONS[label]
        fields = src.filterable_fields(name)
        missing = [f for f in _REQUIRED_V2 if f not in fields]
        if missing and not allow_legacy:
            print(
                f"[{label}] source index lacks {missing} (pre-v2). Rebuild it first "
                f"(`RAG_STORE={src_name} python -m rag.ingest.run --rebuild`) or pass --allow-legacy.",
                file=sys.stderr,
            )
            return 3

    for label in collections:
        name = _COLLECTIONS[label]
        if rebuild:
            # 连同索引级属性(如 origin_fp)一起搬过去,否则目标库的下推会被判为"指纹不符"
            dst.reset_collection(name, properties=src.index_properties(name))
        t0 = time.perf_counter()
        n = 0
        for ids, vecs, metas, docs in src.export(name, batch_size=batch):
            if label == "text":
                dst.upsert_text(
                    [Doc(id=i, text=(d if d is not None else str(m.get("text", ""))), metadata=m) for i, m, d in zip(ids, metas, docs)],
                    vecs,
                )
            else:
                dst.upsert_image(ids, vecs, metas)
            n += len(ids)
        if hasattr(dst, "seal"):
            dst.seal(name)  # 写完即封口:索引在本(无 torch)进程建好并落盘
        dt = time.perf_counter() - t0
        count = dst.text_count() if label == "text" else dst.image_count()
        print(f"[{label}] {src_name} -> {dst_name}: copied {n} vectors in {dt:.1f}s; target now has {count}")
        if count != n and rebuild:
            print(f"[{label}] WARNING: target count {count} != copied {n}", file=sys.stderr)
            return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, choices=SUPPORTED_BACKENDS)
    ap.add_argument("--to", dest="dst", required=True, choices=SUPPORTED_BACKENDS)
    ap.add_argument("--collections", default="text,image", help="comma list of: text,image")
    ap.add_argument("--rebuild", action="store_true", help="drop target collections before copying")
    ap.add_argument("--allow-legacy", action="store_true", help="copy even if the source index predates schema v2")
    ap.add_argument("--batch", type=int, default=1000)
    args = ap.parse_args(argv)
    cols = [c.strip() for c in args.collections.split(",") if c.strip()]
    bad = [c for c in cols if c not in _COLLECTIONS]
    if bad:
        ap.error(f"unknown collection(s): {bad}")
    return migrate(args.src, args.dst, cols, rebuild=args.rebuild, allow_legacy=args.allow_legacy, batch=args.batch)


if __name__ == "__main__":
    raise SystemExit(main())
