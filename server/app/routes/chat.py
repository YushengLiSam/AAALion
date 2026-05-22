"""Chat streaming endpoint.

If DOUBAO_API_KEY is set in the environment, this hits the real model;
otherwise it falls back to a fixture stream so iOS development can
proceed without the key.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas.chat import ChatMessage, ChatFilters
from app.services.doubao_client import DoubaoClient
from app.services.rag_client import stub_top_k

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: ChatFilters | None = Field(default=None)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


_PROMPT = (
    "你是一名中文电商导购助手。仅基于下面的商品目录回答；目录中没有的商品、价格、优惠绝对不要编造。\n"
    "如果用户提到否定条件（'不要含酒精'、'除了 X 之外'），请从候选集中剔除。\n"
    "回复格式：先给一句话推荐，然后 2-4 条理由（每条 30 字以内）。\n"
    "\n## 商品目录\n{catalog}\n"
)


def _build_catalog(products: list[dict]) -> str:
    lines = []
    for p in products:
        rag = p.get("rag_knowledge", {}) or {}
        lines.append(
            f"- {p.get('product_id')} | {p.get('title')} | {p.get('brand')} | ¥{p.get('base_price')} | "
            f"{(rag.get('marketing_description') or '')[:120]}"
        )
    return "\n".join(lines) if lines else "(空)"


async def _fixture_stream(products: list[dict]) -> AsyncIterator[str]:
    """Fallback when Doubao is not configured."""
    for ch in "好的，为你推荐以下商品（fixture stream，未接通豆包）：":
        yield _sse({"type": "delta", "text": ch})
        await asyncio.sleep(0.03)
    for p in products:
        yield _sse({
            "type": "product_card",
            "product": {
                "product_id": p["product_id"],
                "title": p.get("title"),
                "brand": p.get("brand"),
                "base_price": p.get("base_price"),
                "image_url": None,
            },
        })
        await asyncio.sleep(0.06)
    yield _sse({"type": "done"})


async def _real_stream(client: DoubaoClient, user_text: str, products: list[dict]) -> AsyncIterator[str]:
    system = _PROMPT.format(catalog=_build_catalog(products))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    try:
        async for delta in client.stream_chat(messages):
            yield _sse({"type": "delta", "text": delta})
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "message": str(e), "code": "UPSTREAM"})
        return

    # Emit product cards last so the client can render them after the reasoning text.
    for p in products:
        yield _sse({
            "type": "product_card",
            "product": {
                "product_id": p["product_id"],
                "title": p.get("title"),
                "brand": p.get("brand"),
                "base_price": p.get("base_price"),
                "image_url": None,
            },
        })
    yield _sse({"type": "done"})


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    user_text = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    products = stub_top_k(user_text, k=3)

    client = DoubaoClient()
    gen = _real_stream(client, user_text, products) if client.available else _fixture_stream(products)
    return StreamingResponse(gen, media_type="text/event-stream")
