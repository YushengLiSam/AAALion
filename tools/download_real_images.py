#!/usr/bin/env python3
"""Download `image_url_external` for every real product, save locally, and
rewrite the JSON so the backend serves from `/static/` (more reliable than
relying on third-party CDNs that may rotate hashes or block hotlinking).

Pass `--dry-run` to print what would be done without writing anything.

After running:
    python tools/download_real_images.py
    aaalion ingest        # (optional — image_path change doesn't affect text)
    # restart backend so it serves the newly added /static/ files
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = REPO_ROOT / "data" / "seed"

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

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"


def is_real_product(p: dict) -> bool:
    prov = p.get("provenance") or {}
    return prov.get("source_platform") not in (None, "", "AI-gen (demo)")


def download(url: str, dest: Path, timeout: float = 15.0) -> tuple[bool, str]:
    """Return (ok, note)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "image" not in ctype:
                return False, f"not image (Content-Type: {ctype})"
            data = r.read()
            if len(data) < 1024:
                return False, f"too small ({len(data)} bytes; likely placeholder)"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True, f"{len(data)} bytes"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__ + ": " + str(e)[:80]


def main(dry_run: bool = False) -> None:
    summary = {"ok": 0, "fail": 0, "skip": 0}
    failures: list[tuple[str, str, str]] = []

    for cat_dir in sorted(SEED.iterdir()):
        if not cat_dir.is_dir():
            continue
        data_dir = cat_dir / "data"
        if not data_dir.is_dir():
            continue
        for json_path in sorted(data_dir.glob("*.json")):
            try:
                p = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not is_real_product(p):
                continue
            url = p.get("image_url_external") or ""
            pid = p["product_id"]
            if not url.startswith(("http://", "https://")):
                summary["skip"] += 1
                continue
            ext = ".jpg"
            if url.lower().endswith(".png"):
                ext = ".png"
            elif url.lower().endswith(".webp"):
                ext = ".webp"
            rel_path = f"{cat_dir.name}/images/{pid}{ext}"
            dest = SEED / rel_path

            if dest.exists() and dest.stat().st_size > 1024:
                summary["skip"] += 1
                continue

            if dry_run:
                print(f"  [dry-run] would fetch {url} -> {rel_path}")
                continue

            ok, note = download(url, dest)
            if ok:
                summary["ok"] += 1
                print(f"  ✓ {pid}  ({note})")
                # Update JSON to prefer the local path; keep external_url as fallback metadata.
                p["image_path"] = rel_path
                json_path.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                summary["fail"] += 1
                failures.append((pid, url, note))
                print(f"  ✗ {pid}  ({note})")
            time.sleep(0.4)  # be polite to the CDNs

    print()
    print(f"Summary: ok={summary['ok']}  fail={summary['fail']}  skip={summary['skip']}")
    if failures:
        print("\nFailed downloads (need manual replacement):")
        for pid, url, note in failures:
            print(f"  - {pid}  [{note}]  {url}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
