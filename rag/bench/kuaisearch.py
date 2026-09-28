"""KuaiSearch(快手电商搜索数据集,arXiv 2602.11518)→ 向量库规模压测用的数据。

数据不在仓库里(几个 GB,第三方数据集),先按 docs/VECTOR_STORE.md §7 下载到
``data/external/kuaisearch/``:``items_lite.jsonl``(663 万商品)与 ``relevance.jsonl``
(46,422 条人工标注的 query–商品相关性)。

    python -m rag.bench.kuaisearch embed --n 100000

取 items_lite 的前 n 条商品(文件顺序,可复现),用生产同款模型 bge-small-zh-v1.5
算向量,另外抽样 relevance 里的 query 也算好,分别写成向量文件:

    data/.embeddings/kuaisearch_<n>.npz          商品(ids / 向量 / metadata / 文本)
    data/.embeddings/kuaisearch_queries.npz      query(文本 / 向量)

之后的写库与压测(``rag.bench.scale``)完全不需要 torch。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_DIR = REPO_ROOT / "data" / "external" / "kuaisearch"
ITEMS = DATA_DIR / "items_lite.jsonl"
RELEVANCE = DATA_DIR / "relevance.jsonl"
_GENERIC_BRANDS = {"", "无品牌", "其他/other", "其他", "other", "unknown", "UNKNOWN"}


def tag_for(n: int) -> str:
    return f"{n // 1_000_000}m" if n % 1_000_000 == 0 else (f"{n // 1000}k" if n % 1000 == 0 else str(n))


def iter_items(n: int):
    """文件顺序的前 n 条合法商品。"""
    got = 0
    with ITEMS.open(encoding="utf-8") as fh:
        for line in fh:
            if got >= n:
                break
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not it.get("item_title"):
                continue
            got += 1
            yield it


def _cat(it: dict, level: int) -> str:
    name = str(it.get(f"category_level{level}_name") or "")
    return "" if name.upper() == "UNKNOWN" else name


def to_doc(it: dict) -> tuple[str, str, dict]:
    """KuaiSearch 商品 → (id, 用于向量化的文本, metadata)。字段对齐 LionPick 的 schema v2。

    数据集没有价格,base_price 记 0;品牌国别没有可靠来源,记空串("未知",
    反选下推会放行它,与生产语义一致)。
    """
    title = str(it["item_title"]).strip()
    brand = str(it.get("brand_name") or "").strip()
    l1, l2, l3 = _cat(it, 1), _cat(it, 2), _cat(it, 3)
    parts = [title]
    if brand not in _GENERIC_BRANDS:
        parts.append(brand)
    path = "/".join(x for x in (l1, l2, l3) if x)
    if path:
        parts.append(path)
    text = " ".join(parts)
    meta = {
        "product_id": f"ks_{it['item_id']}",
        "chunk_type": "title",
        "category": l1,
        "sub_category": l3 or l2,
        "brand": brand,
        "brand_country": "",
        "currency": "CNY",
        "base_price": 0.0,
        "text": title,
    }
    return f"ks_{it['item_id']}", text, meta


def sample_queries(n: int, seed: int = 20260927) -> list[str]:
    qs: list[str] = []
    seen: set[str] = set()
    with RELEVANCE.open(encoding="utf-8") as fh:
        for line in fh:
            q = str(json.loads(line).get("query") or "").strip()
            if q and q not in seen:
                seen.add(q)
                qs.append(q)
    random.Random(seed).shuffle(qs)
    return qs[:n]


def _encode(texts: list[str], batch: int) -> tuple[np.ndarray, float]:
    from rag.ingest.embed_text import _model  # 生产同款模型与设备

    model = _model()
    t0 = time.perf_counter()
    vecs = model.encode(
        texts, batch_size=batch, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True
    ).astype(np.float32)
    return vecs, time.perf_counter() - t0


def cmd_embed(args) -> int:
    from rag.store.load import ARTIFACT_DIR, save_artifact

    if not ITEMS.exists():
        print(f"missing {ITEMS}; download KuaiSearch first (see module docstring)", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    ids, texts, metas = [], [], []
    for it in iter_items(args.n):
        i, t, m = to_doc(it)
        ids.append(i)
        texts.append(t)
        metas.append(m)
    t_read = time.perf_counter() - t0
    print(f"[kuaisearch] read {len(ids):,} items in {t_read:.1f}s")

    vecs = np.empty((len(texts), 0), dtype=np.float32)
    chunks = []
    t_enc = 0.0
    step = max(args.batch * 64, 10_000)
    for s in range(0, len(texts), step):
        v, dt = _encode(texts[s : s + step], args.batch)
        chunks.append(v)
        t_enc += dt
        done = min(s + step, len(texts))
        print(f"  embedded {done:,}/{len(texts):,}  ({done / t_enc:,.0f} items/s)", flush=True)
    vecs = np.concatenate(chunks) if chunks else vecs
    print(f"[kuaisearch] embedding: {len(texts):,} items in {t_enc:.1f}s = {len(texts) / t_enc:,.0f} items/s "
          f"(dim={vecs.shape[1]})")

    out = save_artifact(
        ARTIFACT_DIR / f"kuaisearch_{tag_for(len(ids))}.npz",
        ids, vecs, metas, [m["text"] for m in metas],
        properties={"dataset": "KuaiSearch items_lite", "n": str(len(ids))},
    )
    print(f"[kuaisearch] saved {out.relative_to(REPO_ROOT)} ({out.stat().st_size / 1e6:,.0f} MB)")

    qpath = ARTIFACT_DIR / "kuaisearch_queries.npz"
    if not qpath.exists() or args.requery:
        qs = sample_queries(args.queries)
        qv, qdt = _encode(qs, args.batch)
        np.savez(qpath, texts=np.asarray(qs, dtype=str), vectors=qv)
        print(f"[kuaisearch] saved {len(qs)} query vectors → {qpath.relative_to(REPO_ROOT)} ({qdt:.1f}s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("embed", help="embed the first N items (and sampled queries)")
    e.add_argument("--n", type=int, default=100_000)
    e.add_argument("--batch", type=int, default=256)
    e.add_argument("--queries", type=int, default=300)
    e.add_argument("--requery", action="store_true", help="re-embed the query sample even if it exists")
    args = ap.parse_args(argv)
    return cmd_embed(args)


if __name__ == "__main__":
    raise SystemExit(main())
