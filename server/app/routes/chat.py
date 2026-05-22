"""Chat streaming endpoint.

The current implementation streams a fixture so the iOS client can be
developed in parallel with the real Doubao + RAG wiring. Replace
``_fake_stream`` with a real Doubao+RAG call once Tujie's retrieval and
Sam's Doubao client are landed (services/rag_client.py + services/doubao_client.py).
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas.chat import ChatMessage, ChatFilters

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: ChatFilters | None = Field(default=None)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _fake_stream() -> AsyncIterator[str]:
    """Hard-coded fixture stream — replace with real Doubao + RAG output."""
    sample_text = "好的，为你推荐这款洁面产品。"
    for ch in sample_text:
        yield _sse({"type": "delta", "text": ch})
        await asyncio.sleep(0.04)

    yield _sse({
        "type": "product_card",
        "product": {
            "product_id": "p_beauty_004",
            "title": "示例洁面产品（占位）",
            "brand": "TBD",
            "base_price": 89.0,
            "image_url": "http://localhost:8000/static/1_%E7%BE%8E%E5%A6%86%E6%8A%A4%E8%82%A4/images/p_beauty_004_live.jpg",
        },
    })
    yield _sse({"type": "done"})


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    # TODO(sam): swap _fake_stream for real Doubao + RAG orchestration.
    return StreamingResponse(_fake_stream(), media_type="text/event-stream")
