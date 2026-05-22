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
from app.services.rag_client import top_k, top_k_image

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


def _extract_user_text(messages) -> str:
    """Pull the text portion of the last user message regardless of shape.
    Multimodal messages have a list of content parts; we concat the text parts."""
    for m in reversed(messages):
        if m.role != "user":
            continue
        content = m.content
        if isinstance(content, str):
            return content
        # list of ContentPart — only text parts are useful for retrieval embedding
        text_chunks = []
        for part in content:
            if hasattr(part, "type") and part.type == "text" and getattr(part, "text", None):
                text_chunks.append(part.text)
        return "\n".join(text_chunks) or "(image-only query)"
    return ""


def _has_image(messages) -> bool:
    for m in reversed(messages):
        if m.role != "user":
            continue
        if isinstance(m.content, list):
            return any(getattr(p, "type", None) == "image_url" for p in m.content)
        return False
    return False


def _extract_image_bytes(messages) -> bytes | None:
    """Pull the JPEG bytes out of the last user message's first image_url part.
    Returns the raw bytes from a data:image/...;base64,... URL, or None."""
    import base64
    for m in reversed(messages):
        if m.role != "user":
            continue
        if not isinstance(m.content, list):
            return None
        for part in m.content:
            if getattr(part, "type", None) == "image_url":
                url = getattr(getattr(part, "image_url", None), "url", "") or ""
                if url.startswith("data:") and ";base64," in url:
                    try:
                        b64 = url.split(";base64,", 1)[1]
                        return base64.b64decode(b64)
                    except Exception:
                        return None
        return None
    return None


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    user_text = _extract_user_text(req.messages)
    embed_query = user_text if user_text else "拍照找货"

    # When the user sends an image, prefer CLIP-based visual retrieval —
    # that's the "拍照找货" rubric path. Falls back to text retrieval if
    # CLIP isn't wired (e.g. running without torch).
    products: list[dict] = []
    if _has_image(req.messages):
        img_bytes = _extract_image_bytes(req.messages)
        if img_bytes:
            products = top_k_image(img_bytes, k=3)
    if not products:
        products = top_k(embed_query, k=3)

    provider = get_provider()
    if _has_image(req.messages):
        # Multimodal path: rebuild the prompt to keep the system text-only,
        # then pass the user's original content (text + image parts) to the LLM.
        # The vision-capable model receives both the image and the catalog context.
        last_user = next((m for m in reversed(req.messages) if m.role == "user"), None)
        system = _PROMPT.format(catalog=_build_catalog(products))
        history = [
            {"role": "system", "content": system},
            {"role": "user", "content": last_user.model_dump()["content"] if last_user else user_text},
        ]
        return StreamingResponse(
            _stream_with_history(provider, history, products),
            media_type="text/event-stream",
        )
    return StreamingResponse(_stream(provider, user_text, products), media_type="text/event-stream")


async def _stream_with_history(provider, history: list[dict], products: list[dict]) -> AsyncIterator[str]:
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
