#!/usr/bin/env python3
"""Offline mock backend for iOS development.

Same shape as the real server's /chat/stream — emits a few delta tokens
then a few product cards, then `done`. Does NOT call Doubao or Qdrant.

Run: python tools/mock_backend.py
Then point the iOS app at http://localhost:8000.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import AsyncIterator

try:
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    import uvicorn
except ImportError:
    sys.stderr.write("Install: pip install fastapi uvicorn\n")
    sys.exit(1)


app = FastAPI(title="LionPick mock backend")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


SAMPLE_PRODUCTS = [
    {"product_id": "p_beauty_004", "title": "薇诺娜舒敏控油洁面乳 100g", "brand": "薇诺娜", "base_price": 89.0, "image_url": None},
    {"product_id": "p_beauty_007", "title": "理肤泉清痘净肤洁面泡沫 200ml", "brand": "理肤泉", "base_price": 145.0, "image_url": None},
    {"product_id": "p_beauty_010", "title": "倩碧液体净肤皂混合至油皮 200ml", "brand": "倩碧", "base_price": 220.0, "image_url": None},
]

REPLY = "好的，为你推荐三款适合油皮的洁面产品，按价格从低到高排序："


async def _gen() -> AsyncIterator[str]:
    for ch in REPLY:
        yield _sse({"type": "delta", "text": ch})
        await asyncio.sleep(0.03)
    for product in SAMPLE_PRODUCTS:
        yield _sse({"type": "product_card", "product": product})
        await asyncio.sleep(0.08)
    yield _sse({"type": "done"})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mock": True}


@app.post("/chat/stream")
async def chat_stream(body: dict | None = None) -> StreamingResponse:
    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.get("/products/{product_id}")
async def get_product(product_id: str) -> dict:
    for p in SAMPLE_PRODUCTS:
        if p["product_id"] == product_id:
            return p
    return {"detail": "not found"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
