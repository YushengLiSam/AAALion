#!/usr/bin/env python3
"""Import real-product research output into canonical seed JSON files.

Round 6 (2026-05-24) added real, verifiable products to the catalog —
sourced via Perplexity / web research, not LLM-generated. This script
takes the structured agent output (JSON list of products) and emits
one JSON file per product under `data/seed/<N>_<category>/data/`,
matching the schema of the existing AI-gen seed products so the rest
of the pipeline (ingest → RAG → SSE → iOS) doesn't need to change.

Usage:
    python tools/import_real_products.py path/to/research_output.json

Each input record needs at minimum:
    product_id, title (Chinese display), brand, category, sub_category,
    base_price, image_url_external, external_url, provenance, marketing_description.

Optional: title_en, user_reviews_summary.

The output JSON gets:
    - all input fields preserved
    - `image_path: null` (we don't download to local; backend serves
      `image_url_external` as-is since Round 6's `_image_url(p)` handles it)
    - canonical `skus` (single SKU at base_price)
    - `rag_knowledge` block with marketing_description + a 1-entry
      user_reviews list summarized from the agent's review_summary
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CATEGORY_DIR_MAP = {
    "美妆护肤": "1_美妆护肤",
    "数码电子": "2_数码电子",
    "服饰运动": "3_服饰运动",
    "食品生活": "4_食品生活",
    "母婴健康": "5_母婴健康",
    "家居家具": "6_家居家具",
    "图书音像": "7_图书音像",
    "户外运动": "8_户外运动",
}


def _slugify_pid(pid: str) -> str:
    # Safe filename. product_id should already be alnum + underscore.
    return re.sub(r"[^A-Za-z0-9_-]", "_", pid)


def _to_canonical(entry: dict) -> dict:
    """Convert a research-agent product record into the seed JSON shape."""
    pid = entry["product_id"]
    title = entry.get("title_zh") or entry["title"]
    title_en = entry.get("title_en") or (entry["title"] if entry.get("title") != title else None)
    prov = entry.get("provenance", {})
    base_price = float(entry["base_price"])

    # One SKU at base price; the existing pipeline reads `skus` but the
    # canonical UI always shows `base_price` anyway.
    sku = {
        "sku_id": f"s_{pid}_1",
        "properties": {"规格": "标准"},
        "price": base_price,
    }

    review_summary = entry.get("user_reviews_summary", "").strip()
    reviews = []
    if review_summary:
        reviews.append({
            "nickname": "海外/真人评论摘要",
            "rating": 4,
            "content": review_summary,
        })

    out = {
        "product_id": pid,
        "title": title,
        "brand": entry["brand"],
        "category": entry["category"],
        "sub_category": entry.get("sub_category", ""),
        "base_price": base_price,
        # Real products: keep absolute URL in image_url_external; backend
        # serves it as the SSE event's image_url. No local image_path.
        "image_path": None,
        "image_url_external": entry.get("image_url_external"),
        "provenance": {
            "origin_country": prov.get("origin_country", "US"),
            "source_platform": prov.get("source_platform", "Amazon US"),
            "currency": prov.get("currency", "USD"),
            "external_url": entry.get("external_url"),
            "shipping_note": prov.get("shipping_note"),
        },
        "skus": [sku],
        "rag_knowledge": {
            "marketing_description": entry.get("marketing_description", ""),
            "official_faq": [],
            "user_reviews": reviews,
        },
    }
    if title_en:
        out["title_en"] = title_en
    return out


def import_file(input_path: Path) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"Expected a JSON list of products, got {type(payload).__name__}")

    count = 0
    for entry in payload:
        category_zh = entry.get("category")
        if category_zh not in CATEGORY_DIR_MAP:
            print(f"  skip {entry.get('product_id')} (unknown category {category_zh!r})", file=sys.stderr)
            continue
        cat_dir = REPO_ROOT / "data" / "seed" / CATEGORY_DIR_MAP[category_zh] / "data"
        cat_dir.mkdir(parents=True, exist_ok=True)
        out_path = cat_dir / f"{_slugify_pid(entry['product_id'])}.json"
        out_path.write_text(json.dumps(_to_canonical(entry), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  wrote {out_path.relative_to(REPO_ROOT)}")
        count += 1
    print(f"\nImported {count} real products from {input_path.name}", file=sys.stderr)
    return count


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: import_real_products.py <input.json>", file=sys.stderr)
        sys.exit(2)
    for arg in sys.argv[1:]:
        path = Path(arg).resolve()
        if not path.is_file():
            print(f"file not found: {path}", file=sys.stderr)
            sys.exit(1)
        import_file(path)
