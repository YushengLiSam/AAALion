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
from app.services.llm_provider import get_provider
from app.services.rag_client import top_k

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


def _image_url(product_id: str, image_path: str | None) -> str | None:
    if not image_path:
        return None
    # data/seed/<category>/images/<file>.jpg is mounted at /static/
    return f"/static/{image_path}"


async def _stream(provider, user_text: str, products: list[dict]) -> AsyncIterator[str]:
    system = _PROMPT.format(catalog=_build_catalog(products))
    history = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    try:
        async for delta in provider.stream_chat(history):
            yield _sse({"type": "delta", "text": delta})
    except Exception as e:  # noqa: BLE001
        yield _sse({"type": "error", "message": str(e), "code": "UPSTREAM"})
        return

    for p in products:
        yield _sse({
            "type": "product_card",
            "product": {
                "product_id": p["product_id"],
                "title": p.get("title"),
                "brand": p.get("brand"),
                "base_price": p.get("base_price"),
                "image_url": _image_url(p["product_id"], p.get("image_path")),
            },
        })
    yield _sse({"type": "done"})


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    user_text = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    products = top_k(user_text, k=3)

    provider = get_provider()
    return StreamingResponse(_stream(provider, user_text, products), media_type="text/event-stream")
