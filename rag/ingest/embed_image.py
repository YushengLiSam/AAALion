"""Embed product images with OpenCLIP ViT-B/32 on the A100 (or any host).

Output: each image gets a 512-d normalized vector, keyed by product_id
(parsed from the filename — `p_beauty_001_live.jpg` → `p_beauty_001`).

This module is import-safe — model loading is lazy, so machines without
torch/open_clip can still import the chunking utilities next door.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable


def _product_id(image_path: Path) -> str | None:
    # Two naming conventions live in data/seed/:
    #   * AI-gen seed:        p_<cat>_NNN_live.jpg   → strip _live  → p_<cat>_NNN
    #   * Round 6 real prods: p_<cat>_real_NN.jpg
    #                         p_<cat>_intl_NN.jpg    → use stem directly
    # Before the second pattern was added, 45 real-product images were silently
    # skipped by image ingest, leaving "拍照找货" non-functional on the Round 6
    # additions that the demo actually shows. Bug fix: R8.F.
    stem = image_path.stem
    if stem.endswith("_live"):
        stem = stem[: -len("_live")]
    if re.match(r"^p_[a-z0-9_]+$", stem):
        return stem
    return None


@lru_cache(maxsize=1)
def _model():
    import torch
    import open_clip
    device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"[clip] loading ViT-B-32 (laion2b_s34b_b79k) on {device}")
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model = model.to(device).eval()
    return model, preprocess, device


def embed_image_file(path: Path) -> list[float]:
    from PIL import Image
    import torch
    model, preprocess, device = _model()
    img = Image.open(path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = model.encode_image(tensor)
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return [float(x) for x in feat.squeeze(0).cpu().tolist()]


def embed_image_bytes(data: bytes) -> list[float]:
    import io
    from PIL import Image
    import torch
    model, preprocess, device = _model()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = model.encode_image(tensor)
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return [float(x) for x in feat.squeeze(0).cpu().tolist()]


def embed_text_query(text: str) -> list[float]:
    """For symmetric retrieval: 'show me a candle' → image vectors."""
    import open_clip
    import torch
    model, _, device = _model()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    tokens = tokenizer([text]).to(device)
    with torch.no_grad():
        feat = model.encode_text(tokens)
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return [float(x) for x in feat.squeeze(0).cpu().tolist()]


def iter_product_images(seed_root: Path) -> Iterable[tuple[str, Path]]:
    for path in sorted(seed_root.glob("*/images/*.jpg")):
        pid = _product_id(path)
        if pid is None:
            continue
        yield pid, path
