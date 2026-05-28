"""Multimodal image-retrieval eval ("拍照找货" bonus track 4.2).

Builds an image-side companion to the text-side ``golden.jsonl`` eval. The
catalog has 145 product images, each labeled with category / brand / id;
we use each as a query against the ``products_image`` Chroma collection and
score whether the top-K returns:

  * ``self_recall@1`` / ``self_recall@5`` — does the same product id come
    back? Sanity check; we expect ~1.0 because the query vector is already
    in the index. Confirms the CLIP encoder + Chroma plumbing work.

  * ``category_precision@5_excl_self`` — of top-5 (excluding the query
    image itself), what fraction belong to the query's catalog category?
    This is the *honest* metric for "given a new user photo, would the
    retrieved candidates be in a plausible category." Realistic upper
    bound for "拍照找货" demo quality.

  * ``brand_recall@5`` — same, for brand. Only computed on the subset of
    queries with a non-empty brand field (Round 6 real products mostly).

The eval is a "leave-one-in" oracle: queries are already in the index. This
inflates self-recall but is a defensible smoke test until we collect a
held-out user-photo set. The disclaimer is reproduced in EVAL_RESULTS.md.

Usage::

    source .venv/bin/activate
    CHROMA_TELEMETRY=False python -m rag.eval.image_eval

Optional flags:
    --k N     change top-K (default 5)
    --json    emit JSON instead of markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_product_catalog() -> dict[str, dict]:
    """Walk ``data/seed/*/data/*.json`` → {product_id: {category, brand, ...}}."""
    seed = REPO_ROOT / "data" / "seed"
    catalog: dict[str, dict] = {}
    for jp in seed.glob("*/data/*.json"):
        try:
            p = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = p.get("product_id") or jp.stem
        catalog[pid] = {
            "category": p.get("category", ""),
            "sub_category": p.get("sub_category", ""),
            "brand": (p.get("brand") or "").strip(),
            "title": p.get("title", ""),
            "image_path": p.get("image_path", ""),
        }
    return catalog


def _iter_indexed_images(catalog: dict[str, dict]) -> list[tuple[str, Path]]:
    """Return [(product_id, image_path)] for every catalog entry that has an
    on-disk image AND a parseable id (matching the ingest filter)."""
    from rag.ingest.embed_image import iter_product_images

    pairs = list(iter_product_images(REPO_ROOT / "data" / "seed"))
    # Restrict to ids the catalog knows about (defensive).
    keep: list[tuple[str, Path]] = []
    for pid, path in pairs:
        if pid in catalog:
            keep.append((pid, path))
    return keep


def _score_query(
    *,
    query_pid: str,
    query_meta: dict,
    hits: list[dict],
    k: int,
) -> dict:
    """Compute metrics for one image query."""
    top_ids = [h["product_id"] for h in hits[:k]]
    in_top1 = top_ids[:1] == [query_pid]
    in_topk = query_pid in top_ids

    # Same-category precision @ k, excluding the query image itself.
    cat = query_meta.get("category", "")
    others = [pid for pid in top_ids if pid != query_pid]
    others_meta = [hits[i] for i in range(len(hits[:k])) if hits[i]["product_id"] != query_pid]
    same_cat = sum(1 for h in others_meta if h.get("category") == cat) if cat else 0
    cat_prec = (same_cat / len(others_meta)) if others_meta else 0.0

    # Same-brand recall (only meaningful when query has a brand).
    brand = (query_meta.get("brand") or "").lower()
    brand_recall = None
    if brand:
        same_brand = sum(
            1
            for h in others_meta
            if (h.get("brand") or "").lower() == brand
        )
        brand_recall = (same_brand / len(others_meta)) if others_meta else 0.0

    return {
        "self_recall@1": 1.0 if in_top1 else 0.0,
        "self_recall@k": 1.0 if in_topk else 0.0,
        "category_precision@k_excl_self": cat_prec,
        "brand_recall@k_excl_self": brand_recall,
    }


def run(k: int = 5) -> dict:
    """Execute the eval; return a structured dict for printing or JSON dump."""
    os.environ.setdefault("CHROMA_TELEMETRY", "False")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

    from rag.ingest.embed_image import embed_image_bytes
    from rag.store import query_image as store_query_image

    catalog = _load_product_catalog()
    pairs = _iter_indexed_images(catalog)
    if not pairs:
        raise SystemExit(
            "No indexed product images found. Build the image index first:\n"
            "    python -m rag.ingest.run_image"
        )

    per_case: list[dict] = []
    by_cat: dict[str, list[dict]] = defaultdict(list)
    latencies_ms: list[float] = []

    for pid, path in pairs:
        meta = catalog[pid]
        t0 = time.perf_counter()
        vec = embed_image_bytes(path.read_bytes())
        raw_hits = store_query_image(vec, k=k + 1)  # +1 so we can drop self and still have k.
        # Annotate hits with catalog metadata for downstream scoring.
        # ``store_query_image`` returns ``Hit`` dataclasses; flatten to plain
        # dicts so the rest of this script is uniform.
        hits = []
        for h in raw_hits:
            m = catalog.get(h.id, {})
            hits.append(
                {
                    "product_id": h.id,
                    "score": h.score,
                    "category": m.get("category", ""),
                    "brand": m.get("brand", ""),
                }
            )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(latency_ms)

        m = _score_query(query_pid=pid, query_meta=meta, hits=hits, k=k)
        rec = {
            "product_id": pid,
            "category": meta.get("category", ""),
            "brand": meta.get("brand", ""),
            "top_ids": [h["product_id"] for h in hits[:k]],
            "latency_ms": round(latency_ms, 1),
            **m,
        }
        per_case.append(rec)
        by_cat[meta.get("category", "(uncategorized)")].append(rec)

    def _mean_of(rec_list: list[dict], field: str) -> float | None:
        vs = [r[field] for r in rec_list if r.get(field) is not None]
        return round(mean(vs), 3) if vs else None

    overall = {
        "n_queries": len(per_case),
        "self_recall@1": _mean_of(per_case, "self_recall@1"),
        "self_recall@k": _mean_of(per_case, "self_recall@k"),
        "category_precision@k_excl_self": _mean_of(per_case, "category_precision@k_excl_self"),
        "brand_recall@k_excl_self": _mean_of(per_case, "brand_recall@k_excl_self"),
        "latency_ms_mean": round(mean(latencies_ms), 1) if latencies_ms else None,
        "latency_ms_p50": round(median(latencies_ms), 1) if latencies_ms else None,
        "latency_ms_p95": round(sorted(latencies_ms)[int(len(latencies_ms) * 0.95)], 1)
        if latencies_ms
        else None,
    }

    per_category = {}
    for cat, recs in sorted(by_cat.items()):
        per_category[cat] = {
            "n": len(recs),
            "self_recall@1": _mean_of(recs, "self_recall@1"),
            "self_recall@k": _mean_of(recs, "self_recall@k"),
            "category_precision@k_excl_self": _mean_of(recs, "category_precision@k_excl_self"),
            "brand_recall@k_excl_self": _mean_of(recs, "brand_recall@k_excl_self"),
        }

    return {
        "k": k,
        "overall": overall,
        "per_category": per_category,
        "per_case": per_case,
    }


def _format_markdown(result: dict) -> str:
    k = result["k"]
    o = result["overall"]
    lines = [
        f"### 多模态图像检索(拍照找货,leave-one-in,k={k},queries={o['n_queries']})",
        "",
        "| 指标 | 数值 | 释义 |",
        "|---|---:|---|",
        f"| `self_recall@1` | **{o['self_recall@1']:.3f}** | top-1 命中查询商品自身;CLIP + Chroma 路径正确性 sanity |",
        f"| `self_recall@{k}` | **{o[f'self_recall@k']:.3f}** | top-{k} 命中自身 |",
        f"| `category_precision@{k}_excl_self` | **{o[f'category_precision@k_excl_self']:.3f}** | 排除自身后 top-{k} 中同类目占比 — 真实「相似品类召回」信号 |",
    ]
    if o.get("brand_recall@k_excl_self") is not None:
        lines.append(
            f"| `brand_recall@{k}_excl_self` | **{o['brand_recall@k_excl_self']:.3f}** | 排除自身后 top-{k} 中同品牌占比 |"
        )
    lines.append(
        f"| 延迟 | mean={o['latency_ms_mean']}ms · p50={o['latency_ms_p50']}ms · p95={o['latency_ms_p95']}ms | OpenCLIP ViT-B/32 编码 + Chroma cosine on MPS |"
    )
    lines.append("")
    lines.append("#### 按类目拆解")
    lines.append("")
    lines.append("| 类目 | n | self_recall@1 | self_recall@k | category_precision@k_excl_self |")
    lines.append("|---|---:|---:|---:|---:|")
    for cat, c in result["per_category"].items():
        lines.append(
            f"| {cat or '(空)'} | {c['n']} | {c['self_recall@1']:.3f} | {c[f'self_recall@k']:.3f} | {c[f'category_precision@k_excl_self']:.3f} |"
        )
    lines.append("")
    lines.append(
        "> **方法学说明 / 已知局限**:这是 *leave-one-in* 评测 —— 查询图就在索引里,所以 "
        "`self_recall@1` 接近 1.0 是预期的(它只检验 CLIP 编码 + Chroma 检索链路本身没坏)。"
        "真正的「拍照找货」指标是 `category_precision@k_excl_self` —— 排除自身后 top-k 是否落在合理类目里。"
        "更严格的 held-out user-photo 评测在 06-10 前再补;现阶段先把 *bonus track 4.2* 从「只有截图」升级为「有数字」。"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = ap.parse_args()

    result = run(k=args.k)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
