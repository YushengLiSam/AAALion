"""Product detail endpoint, served from the indexed JSON files."""

from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.currency import normalize_product_price

router = APIRouter(prefix="/products", tags=["products"])


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    seed = settings.repo_root / "data" / "seed"
    index: dict[str, Path] = {}
    for json_path in seed.glob("*/data/*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        product_id = data.get("product_id")
        if isinstance(product_id, str):
            index[product_id] = json_path
    return index


@router.get("/{product_id}")
async def get_product(product_id: str) -> dict:
    path = _index().get(product_id)
    if path is None:
        raise HTTPException(status_code=404, detail="product not found")
    product = json.loads(path.read_text(encoding="utf-8"))
    return await asyncio.to_thread(normalize_product_price, product)
