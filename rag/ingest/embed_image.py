"""Embed product images with OpenCLIP ViT-B/32 on the A100.

Run this only on the A100 (or any host with a CUDA GPU). Output vectors
go into the Qdrant ``products_image`` collection.
"""

from __future__ import annotations

from pathlib import Path


def embed_images(image_dir: Path) -> list[tuple[str, list[float]]]:
    """Embed every JPG/PNG in ``image_dir`` recursively.

    TODO(tujie):
      - ``pip install open_clip_torch torch torchvision``.
      - Load ``ViT-B-32`` with the ``laion2b_s34b_b79k`` pretrained weights.
      - Stream-batch the images at batch_size=32.
      - Return [(product_id, vector), ...] where product_id is derived
        from the filename stem (strip the ``_live`` suffix).

    This is a stub until we have GPU access wired up in CI.
    """
    return []
