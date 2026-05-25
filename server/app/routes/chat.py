"""Chat streaming endpoint.

Pipeline:
  1. Extract last user text + optional image bytes.
  2. Retrieve top candidates (CLIP if image; hybrid+rerank otherwise).
  3. Build catalog block + system prompt; assemble messages.
  4. Cache check (hash of system + messages + image sha) → replay if hit.
  5. Stream from the LLM provider with retry/backoff on upstream errors.
  6. Emit product cards + intent events; close with `done`.
  7. Log structured timing (received → retrieval → first delta → done).

Cancellation: if the client disconnects mid-stream, we stop calling the
LLM and exit the generator so we don't burn quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas.chat import ChatMessage, ChatFilters
from app.services.llm_provider import get_provider
from app.services.rag_client import top_k, top_k_image
from app.services.cache import cache, make_key, hash_image_bytes
from app.services.contextual_query import build_retrieval_query
from app.services.currency import normalize_product_prices, pricing_cache_token

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger("chat")


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: ChatFilters | None = Field(default=None)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# Tightened system prompt: commits on visual matches, structured filtering rule.
_PROMPT = (
    "你是一名中文电商导购助手。仅基于下面的商品目录回答；目录中没有的商品、价格、优惠绝对不要编造。\n"
    "\n"
    "规则：\n"
    "1. 如果用户上传了图片，你的视觉识别（品牌+品类）若能匹配目录中的某款商品，"
    "**直接确认这就是该商品**，不要含糊地说『目录中没有』。\n"
    "2. 如果用户说『不要 X』『除了 X』『不含 X』，从候选集中**剔除**含 X 的商品。\n"
    "3. 多商品对比：从『价格、关键成分、适用场景、优劣势』中选择 3-5 个维度，逐条列出。\n"
    "4. 回复格式：先一句话推荐，再 2-4 条理由（每条 ≤ 30 字），最后可选一句替代建议。\n"
    "\n## 商品目录\n{catalog}\n"
)


def _build_catalog(products: list[dict]) -> str:
    lines = []
    for p in products:
        rag = p.get("rag_knowledge", {}) or {}
        lines.append(
            f"- {p.get('product_id')} | {p.get('title')} | {p.get('brand')} | {_catalog_price(p)} | "
            f"{(rag.get('marketing_description') or '')[:120]}"
        )
    return "\n".join(lines) if lines else "(空)"


def _catalog_price(product: dict) -> str:
    """Price wording supplied to the LLM, matching the product card display."""
    price_cny = product.get("price_cny")
    if price_cny is not None:
        text = f"¥{float(price_cny):.2f}"
        rate = product.get("exchange_rate")
        if isinstance(rate, dict):
            provenance = product.get("provenance") or {}
            source = provenance.get("currency", rate.get("source_currency", ""))
            text += (
                f" (原价 {source} {float(product.get('base_price', 0)):.2f}; "
                f"参考汇率日期 {rate.get('rate_date', '')})"
            )
        return text
    provenance = product.get("provenance") or {}
    return f"{provenance.get('currency', 'CNY')} {product.get('base_price')} (汇率暂不可用)"


def _image_url(p: dict) -> str | None:
    """Resolve image URL for a product.

    Priority:
      1. Local `image_path` under data/seed/<cat>/images/ — most reliable,
         served via /static/ by FastAPI. Used by AI-gen seed AND by
         real-product entries that have an AI-rendered placeholder image
         (see tools/generate_product_images.py).
      2. Absolute `image_url_external` (Amazon/JD CDN) — only used when no
         local image exists. Often 404s for real products because the
         research agents inferred image hashes that couldn't be verified
         against live pages.
    """
    image_path = p.get("image_path")
    if image_path:
        return f"/static/{image_path}"
    ext = p.get("image_url_external")
    if ext and isinstance(ext, str) and ext.startswith(("http://", "https://")):
        return ext
    return None


# Sensible default provenance for AI-gen seed products that have no explicit
# `provenance` block. Surfaced to iOS so the card renders a "演示" badge.
_DEMO_PROVENANCE = {
    "origin_country": "CN",
    "source_platform": "AI-gen (demo)",
    "currency": "CNY",
    "external_url": None,
    "shipping_note": None,
}


def _provenance(p: dict) -> dict:
    """Read provenance from the product JSON; fall back to AI-gen marker."""
    raw = p.get("provenance")
    if not isinstance(raw, dict):
        return _DEMO_PROVENANCE
    return {
        "origin_country": raw.get("origin_country", "CN"),
        "source_platform": raw.get("source_platform", "AI-gen (demo)"),
        "currency": raw.get("currency", "CNY"),
        "external_url": raw.get("external_url"),
        "shipping_note": raw.get("shipping_note"),
    }


def _product_card_event(p: dict) -> dict:
    return {
        "type": "product_card",
        "product": {
            "product_id": p["product_id"],
            "title": p.get("title"),
            "brand": p.get("brand"),
            "base_price": p.get("base_price"),
            "price_cny": p.get("price_cny"),
            "exchange_rate": p.get("exchange_rate"),
            "image_url": _image_url(p),
            "provenance": _provenance(p),
        },
    }


# Intent detection for 4.1 cart flow.
_ADD_TO_CART = re.compile(r"加入?购物?车|加购|加入车|放购物?车")
_CHECKOUT = re.compile(r"下单|结(账|算)|去结算|帮我下个?单|买单")

#读用户是否想checkout
def _detect_cart_intent(text: str) -> dict | None:
    if not text:
        return None
    if _CHECKOUT.search(text):
        return {"type": "cart_intent", "action": "checkout"}
    if _ADD_TO_CART.search(text):
        return {"type": "cart_intent", "action": "add"}
    return None

#user_text 主要给 _detect_cart_intent() 用——判断用户说没说"加购"、"下单"这类关键词。
def _extract_user_text(messages) -> str:
    for m in reversed(messages):
        if m.role != "user":
            continue
        content = m.content
        if isinstance(content, str):
            return content
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

#iPhone 上传图片时，不是直接传文件，而是把图片转成 Base64 字符串塞在 JSON 里。 找原始图片的二进制数据
def _extract_image_bytes(messages) -> bytes | None:
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

#调 LLM 失败了不直接报错，等一会儿再试
async def _stream_chat_with_retry(provider, history: list[dict], max_attempts: int = 3):
    """Async-iterate provider.stream_chat with exponential backoff on early errors."""
    delay = 0.5
    for attempt in range(1, max_attempts + 1):
        try:
            async for delta in provider.stream_chat(history):
                yield delta
            return
        except Exception as e:  # noqa: BLE001
            if attempt == max_attempts:
                raise
            log.warning(f"LLM upstream error (attempt {attempt}/{max_attempts}): {e}; backoff {delay}s")
            await asyncio.sleep(delay)
            delay *= 2


@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    t_received = time.perf_counter()
    user_text = _extract_user_text(req.messages)
    retrieval_query = build_retrieval_query(req.messages)

    # Retrieval ----------------------------------------------------------------
    products: list[dict] = []
    img_bytes: bytes | None = None
    if _has_image(req.messages):
        img_bytes = _extract_image_bytes(req.messages)
        if img_bytes:
            products = top_k_image(img_bytes, k=3)
    if not products:
        explicit_filters = req.filters.model_dump(exclude_none=True) if req.filters else None
        products = top_k(retrieval_query, k=5, filters=explicit_filters)
    products = await asyncio.to_thread(normalize_product_prices, products)
    t_retrieval = time.perf_counter()

    # Cart-intent detection ----------------------------------------------------
    cart_event = _detect_cart_intent(user_text)

    # Cache --------------------------------------------------------------------
    cache_key = make_key(
        system_prompt=f"{_PROMPT[:128]}|fx={pricing_cache_token(products)}",
        messages_json=req.model_dump_json(),
        image_sha=hash_image_bytes(img_bytes),
    )
    cached_events = cache.get(cache_key)

    # Build messages + provider ------------------------------------------------
    system = _PROMPT.format(catalog=_build_catalog(products))
    if _has_image(req.messages):
        last_user = next((m for m in reversed(req.messages) if m.role == "user"), None)
        history = [
            {"role": "system", "content": system},
            {"role": "user", "content": last_user.model_dump()["content"] if last_user else user_text},
        ]
    else:
        history = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
    provider = get_provider()

    async def generator() -> AsyncIterator[str]:
        nonlocal cart_event
        events_to_cache: list[dict] = []
        t_first_delta: float | None = None

        # Cart intent comes first so iOS can update its UI immediately.
        if cart_event:
            yield _sse(cart_event)
            events_to_cache.append(cart_event)

        # Cache hit: replay quickly.
        if cached_events:
            for ev in cached_events:
                if await request.is_disconnected():
                    break
                yield _sse(ev)
                if ev.get("type") == "delta" and t_first_delta is None:
                    t_first_delta = time.perf_counter()
                if ev.get("type") == "delta":
                    await asyncio.sleep(0.015)
            t_done = time.perf_counter()
            _log_timing(
                t_received,
                t_retrieval,
                t_first_delta,
                t_done,
                cache_hit=True,
                user_text=user_text,
                retrieval_query=retrieval_query,
            )
            return

        # Cache miss: stream from LLM with retry/backoff.
        try:
            async for delta in _stream_chat_with_retry(provider, history):
                if await request.is_disconnected():
                    log.info("client disconnected mid-stream; cancelling")
                    return
                ev = {"type": "delta", "text": delta}
                yield _sse(ev)
                events_to_cache.append(ev)
                if t_first_delta is None:
                    t_first_delta = time.perf_counter()
        except Exception as e:  # noqa: BLE001
            err = {"type": "error", "message": str(e), "code": "UPSTREAM"}
            yield _sse(err)
            events_to_cache.append(err)

        for p in products:
            if await request.is_disconnected():
                return
            ev = _product_card_event(p)
            yield _sse(ev)
            events_to_cache.append(ev)

        done = {"type": "done"}
        yield _sse(done)
        events_to_cache.append(done)

        # Don't pollute the cache with error-only streams.
        if any(e.get("type") == "delta" for e in events_to_cache):
            cache.put(cache_key, events_to_cache)

        t_done = time.perf_counter()
        _log_timing(
            t_received,
            t_retrieval,
            t_first_delta,
            t_done,
            cache_hit=False,
            user_text=user_text,
            retrieval_query=retrieval_query,
        )

    return StreamingResponse(generator(), media_type="text/event-stream")


def _log_timing(
    t_received: float,
    t_retrieval: float,
    t_first_delta: float | None,
    t_done: float,
    *,
    cache_hit: bool,
    user_text: str,
    retrieval_query: str,
) -> None:
    record = {
        "event": "chat_stream",
        "cache": "hit" if cache_hit else "miss",
        "retrieval_ms": round((t_retrieval - t_received) * 1000),
        "first_delta_ms": round((t_first_delta - t_received) * 1000) if t_first_delta else None,
        "total_ms": round((t_done - t_received) * 1000),
        "query_preview": user_text[:60],
    }
    if retrieval_query != user_text:
        record["retrieval_query_preview"] = retrieval_query[:100]
    # uvicorn doesn't pipe named-logger info to stdout by default; print
    # so timing shows up next to the access logs without extra config.
    print(json.dumps(record, ensure_ascii=False), flush=True)
