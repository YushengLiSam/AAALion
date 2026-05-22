#!/usr/bin/env python3
"""Generate AI-rendered placeholder product images via TokenRouter for the
real-product entries whose `image_url_external` (from the research agent)
turned out to be a fabricated hash that 404s on the live CDN.

Honest framing: the **product data** (title, brand, price, external URL) is
real — verifiable on Tmall / JD / Amazon. The **image** is an AI-rendered
representative shot rather than the platform's real photo. The JSON gains an
`image_source: "ai-gen-placeholder"` flag and the iOS UI keeps the real
provenance + 「去原页」 button so users can verify the real product themselves.

Why not use the real platform image? Two reasons:
1. The research agent that found these products inferred image hashes from
   search-result snippets (JD blocks US-IP fetches, Amazon hashes rotate).
   Only 2 of 45 hashes turned out to be live (`download_real_images.py`).
2. Scraping live JD pages requires CN-side access or a headless browser
   with cookies; out of scope for a 1-night Round 6 fix.

Usage:
    python tools/generate_product_images.py             # generate missing
    python tools/generate_product_images.py --force     # regenerate all real items
    python tools/generate_product_images.py --limit 5   # cap for cost/testing
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = REPO_ROOT / "data" / "seed"

CATEGORY_HINTS = {
    "美妆护肤": "cosmetic/skincare bottle or jar packaging",
    "数码电子": "consumer electronics device, modern design",
    "服饰运动": "clothing or sneaker on display",
    "食品生活": "consumer-packaged food or snack box",
    "母婴健康": "baby / maternity / health-supplement product packaging",
    "家居家具": "home goods / furniture / household product",
    "图书音像": "book cover (Chinese hardcover, professional book photography)",
    "户外运动": "outdoor / sports gear, athletic product",
}


def credentials() -> tuple[str, str]:
    """Read TokenRouter creds from server/.env or ~/.config/lionpick/credentials.env."""
    candidates = [
        REPO_ROOT / "server" / ".env",
        Path.home() / ".config" / "lionpick" / "credentials.env",
    ]
    key = os.environ.get("TOKENROUTER_API_KEY", "")
    base = os.environ.get("TOKENROUTER_BASE_URL", "")
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TOKENROUTER_API_KEY=") and not key:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("TOKENROUTER_BASE_URL=") and not base:
                base = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("TOKENROUTER_API_KEY not found in env or .env files")
    return key, (base or "https://api.tokenrouter.com/v1")


def is_real_product(p: dict) -> bool:
    prov = p.get("provenance") or {}
    return prov.get("source_platform") not in (None, "", "AI-gen (demo)")


def already_has_local_image(p: dict, json_path: Path) -> bool:
    img_path = p.get("image_path")
    if not img_path:
        return False
    full = SEED / img_path
    return full.is_file() and full.stat().st_size > 1024


def build_prompt(p: dict) -> str:
    """Craft a clear English image-gen prompt from the product JSON."""
    title = p.get("title", "")
    brand = p.get("brand", "")
    cat = p.get("category", "")
    sub = p.get("sub_category", "")
    hint = CATEGORY_HINTS.get(cat, "consumer product")
    parts = [
        "Professional studio product photograph,",
        f"a {hint} that represents the product '{title}'",
        f"from brand {brand}." if brand else ".",
        f"Sub-category: {sub}." if sub else "",
        "Clean pure-white background, soft natural lighting, e-commerce catalog framing,",
        "centered composition, photorealistic, sharp detail.",
        "No text overlay, no captions, no watermark, no logos other than what would naturally appear on the product packaging.",
        "Square aspect ratio.",
    ]
    return " ".join(s for s in parts if s)


def call_image_gen(prompt: str, *, model: str, key: str, base: str, size: str = "1024x1024") -> bytes:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode())
    data_list = payload.get("data") or []
    if not data_list:
        raise RuntimeError(f"no data in response: {str(payload)[:200]}")
    b64 = data_list[0].get("b64_json")
    if not b64:
        raise RuntimeError(f"no b64_json in response: {str(data_list[0])[:200]}")
    return base64.b64decode(b64)


def gather_targets(force: bool) -> list[Path]:
    out: list[Path] = []
    for cat_dir in sorted(SEED.iterdir()):
        if not cat_dir.is_dir():
            continue
        data_dir = cat_dir / "data"
        if not data_dir.is_dir():
            continue
        for jp in sorted(data_dir.glob("*.json")):
            try:
                p = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not is_real_product(p):
                continue
            if (not force) and already_has_local_image(p, jp):
                continue
            out.append(jp)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="regenerate even if local image exists")
    parser.add_argument("--limit", type=int, default=0, help="cap number of generations (0 = no cap)")
    parser.add_argument("--model", default="openai/gpt-5.4-image-2")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    key, base = credentials()
    targets = gather_targets(args.force)
    if args.limit > 0:
        targets = targets[: args.limit]
    print(f"Target count: {len(targets)}")
    if args.dry_run:
        for jp in targets:
            print(f"  would generate {jp.relative_to(REPO_ROOT)}")
        return

    ok = 0
    fail = 0
    for i, jp in enumerate(targets, 1):
        p = json.loads(jp.read_text(encoding="utf-8"))
        pid = p["product_id"]
        cat_dir_name = jp.parent.parent.name
        rel = f"{cat_dir_name}/images/{pid}.jpg"
        dest = SEED / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(p)
        print(f"[{i}/{len(targets)}] {pid}  ({cat_dir_name})")
        try:
            png_bytes = call_image_gen(prompt, model=args.model, key=key, base=base, size=args.size)
            # API returns PNG ~850 KB each. Convert to JPEG q85 to keep the
            # committed seed set under control (45 × 100 KB ≈ 5 MB instead of 38 MB).
            try:
                from io import BytesIO
                from PIL import Image
                im = Image.open(BytesIO(png_bytes)).convert("RGB")
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=85, optimize=True)
                jpg_bytes = buf.getvalue()
            except Exception:  # PIL missing → fall back to raw bytes
                jpg_bytes = png_bytes
            dest.write_bytes(jpg_bytes)
            p["image_path"] = rel
            p["image_source"] = "ai-gen-placeholder"
            jp.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            ok += 1
            print(f"    saved {len(jpg_bytes)} bytes -> {rel}  (was {len(png_bytes)} PNG)")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"    !! {type(e).__name__}: {str(e)[:120]}")
        # Be gentle with the rate limit.
        time.sleep(1.0)

    print(f"\nSummary: ok={ok}  fail={fail}")


if __name__ == "__main__":
    main()
