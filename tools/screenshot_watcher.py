#!/usr/bin/env python3
"""Watch the macOS pasteboard and persist screenshots taken with
shift+ctrl+command+4 (clipboard-copy variant) as PNG files under
the project screenshots directory.

Usage:
    python tools/screenshot_watcher.py            # default output dir
    python tools/screenshot_watcher.py --out DIR  # custom output dir

Stop with Ctrl+C.

Implementation notes:
- We poll NSPasteboard.changeCount() every 250 ms.
- When changeCount increments and the pasteboard carries image data
  (PNG or TIFF) but NO file URL, we treat it as a screenshot.
- A file URL on the pasteboard means the user copied a file in Finder
  or a browser image with metadata, which we deliberately skip.
- TIFF is converted to PNG via NSBitmapImageRep.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from pathlib import Path

try:
    from AppKit import (
        NSPasteboard,
        NSPasteboardTypePNG,
        NSPasteboardTypeTIFF,
        NSPasteboardTypeFileURL,
        NSBitmapImageRep,
        NSBitmapImageFileTypePNG,
    )
except ImportError:
    sys.stderr.write(
        "Missing dependency: pyobjc-framework-Cocoa.\n"
        "Install with:  pip install pyobjc-framework-Cocoa\n"
    )
    sys.exit(1)


POLL_INTERVAL_SEC = 0.25
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "screenshots"


def _filename(now: _dt.datetime, change_count: int) -> str:
    return f"screenshot_{now.strftime('%Y%m%d_%H%M%S')}_{change_count}.png"


def _tiff_to_png(tiff_data) -> bytes | None:
    rep = NSBitmapImageRep.imageRepWithData_(tiff_data)
    if rep is None:
        return None
    png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
    if png is None:
        return None
    return bytes(png)


def _maybe_save(pasteboard, out_dir: Path, change_count: int) -> Path | None:
    # Skip when the pasteboard also carries a file URL.
    if pasteboard.dataForType_(NSPasteboardTypeFileURL) is not None:
        return None

    png_data = pasteboard.dataForType_(NSPasteboardTypePNG)
    if png_data is not None:
        payload = bytes(png_data)
    else:
        tiff_data = pasteboard.dataForType_(NSPasteboardTypeTIFF)
        if tiff_data is None:
            return None
        payload = _tiff_to_png(tiff_data)
        if payload is None:
            return None

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _filename(_dt.datetime.now(), change_count)
    path.write_bytes(payload)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    out_dir = args.out.expanduser().resolve()
    pb = NSPasteboard.generalPasteboard()
    last_count = pb.changeCount()

    print(f"[screenshot_watcher] watching pasteboard → {out_dir}")
    print("[screenshot_watcher] take a screenshot with shift+ctrl+cmd+4. Ctrl+C to stop.")

    try:
        while True:
            current = pb.changeCount()
            if current != last_count:
                last_count = current
                saved = _maybe_save(pb, out_dir, current)
                if saved is not None:
                    print(f"[screenshot_watcher] saved {saved}")
            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\n[screenshot_watcher] stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
