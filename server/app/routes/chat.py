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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas.chat import ChatMessage, ChatFilters
from app.services.llm_provider import get_provider
from app.services.rag_client import top_k, top_k_image
from app.services.cache import cache, make_key, hash_image_bytes_list
from app.services.constraint_state import build_conversation_filter
from app.services.contextual_query import build_retrieval_query
from app.services.currency import normalize_product_prices, pricing_cache_token

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger("chat")


# Pydantic 请求体 schema。
# 客户端 POST /chat/stream 时的 JSON 必须长这样:messages 是完整对话历史(包含本轮),
# filters 是 iOS Settings 里设置的硬过滤(类目/品牌/价格区间,可选)。
# Pydantic 自动验证类型 + 字段名,不符合的 422 直接拒绝,不会进到我们写的业务代码。
class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: ChatFilters | None = Field(default=None)


# 把字典编码成 SSE(Server-Sent Events)标准格式:`data: {json}\n\n`。
# SSE 协议要求每条事件以 "data: " 开头、双换行结尾。iOS 端的 URLSession.bytes.lines
# 按这个分隔符切流,所以这个格式是"协议",不能改。ensure_ascii=False 让中文不变成 \uXXXX。
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


# 把检索回来的 top-K 商品拼成 LLM 能读懂的文本目录。
# 每行一个商品:product_id | 标题 | 品牌 | 价格 | 营销描述前 120 字。
# 这段文本会替换 _PROMPT 里的 {catalog} 占位符进入 system prompt。
# **反幻觉的关键**:LLM 只能从这个目录里挑商品回答,不能编造目录之外的 SKU/价格。
def _build_catalog(products: list[dict]) -> str:
    lines = []
    for p in products:
        rag = p.get("rag_knowledge", {}) or {}
        lines.append(
            f"- {p.get('product_id')} | {p.get('title')} | {p.get('brand')} | {_catalog_price(p)} | "
            f"{(rag.get('marketing_description') or '')[:120]}"
        )
    return "\n".join(lines) if lines else "(空)"


# 给 LLM 看的价格字符串,跟 iOS 商品卡显示一致。
# CNY 商品直接 `¥xxx.xx`;外币商品(price_cny 由 Tujie 的 currency 服务从 Frankfurter
# 实时汇率算出)显示成 `¥xxx.xx (原价 USD xx.xx; 参考汇率日期 2026-05-25)`,
# **透明告诉 LLM 这是换算价、不是用户实付价**,LLM 才不会跟用户说"这件 ¥99"误导成报价。
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


# 决定一个商品卡片在 iOS 上显示哪张图。
# 优先用本地 image_path(走 FastAPI /static/ 路由,最稳定);
# 没本地图才 fallback 到外链 image_url_external(Amazon/JD CDN 经常 404,
# 这是 Round 6 真品时踩过的坑——外链图寿命短)。
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


# 读出商品的 provenance 块(产地国 / 平台 / 货币 / 外链 / 物流提示)。
# Round 6 真品的 JSON 里都带这个块;AI-gen 占位商品没有,fallback 用 _DEMO_PROVENANCE,
# iOS 卡片靠 source_platform == "AI-gen (demo)" 渲染"演示"badge,不让评委误以为是真品。
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


# 把商品 dict 包装成 SSE 事件 `{type: "product_card", product: {...}}`。
# iOS 收到这个事件就在当前聊天气泡下方渲染一张商品卡。
# 一个 LLM 文字回复后通常会跟着 N 个 product_card 事件(N = top-K = 3 或 5)。
# 字段精简到 iOS 需要的字段,不外传内部 rag_knowledge 这些。
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

# 纯正则识别用户意图是不是「加购」或「下单」。
# 匹配到就立刻返回 cart_intent 事件,iOS 收到后**先于 LLM 回复**弹购物车 UI。
# 这是 4.1 题面要求的购物车流——意图识别**不走 LLM**(LLM 慢且不稳),
# 关键词够用。匹配优先级:checkout 比 add 高(用户既说"加购"又说"下单"按下单算)。
def _detect_cart_intent(text: str) -> dict | None:
    if not text:
        return None
    if _CHECKOUT.search(text):
        return {"type": "cart_intent", "action": "checkout"}
    if _ADD_TO_CART.search(text):
        return {"type": "cart_intent", "action": "add"}
    return None

# 从最后一条 user 消息里抽出纯文本(不含图)。
# content 可能是字符串(老格式)或 list[ContentPart](多模态新格式),两种都要处理。
# 用途:(1) 给 _detect_cart_intent 看是不是要加购/下单;(2) 拼日志 query_preview;
# (3) 作为 LLM 的纯文本 fallback。纯图请求返回 "(image-only query)" 占位。
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


# 判断最后一条 user 消息里是不是带了 image_url 类型的 content part。
# 决定走 CLIP 图像检索路径还是 hybrid+rerank 文字检索路径——是整个路由的分叉点。
def _has_image(messages) -> bool:
    for m in reversed(messages):
        if m.role != "user":
            continue
        if isinstance(m.content, list):
            return any(getattr(p, "type", None) == "image_url" for p in m.content)
        return False
    return False

# 把最后一条 user 消息里所有 image_url 的 base64 部分解码成原始 JPEG 字节列表(R8.E 支持多图)。
# iPhone 上传图不是发文件,是把 JPEG 字节做 base64 编码后塞进 `data:image/jpeg;base64,xxx` URL 里。
# 这里反向 b64 decode 出原始字节,三处用:
#   (1) imgs[0] 喂给 CLIP retriever 检索(CLIP 单图)
#   (2) 整个列表算 cache key 的 image_sha(R8.E 用 sorted concat,顺序无关)
#   (3) 喂给 _downscale_message_content 缩小后发 LLM
# cap=10 跟 iOS 端 Attachment.maxCount 对齐,防止恶意构造 payload 撑爆。
def _extract_image_bytes_list(messages, cap: int = 10) -> list[bytes]:
    """R8.E: return ALL inline image bytes from the last user message,
    not just the first. iOS now sends up to `Attachment.maxCount`
    image_url parts; vision-LLMs handle multi-image natively. CLIP
    retrieval still uses imgs[0] (single-image visual retriever); the
    full list goes to the LLM via the content array.

    Capped at `cap` to bound payload size and match the iOS limit.
    """
    import base64

    out: list[bytes] = []
    for m in reversed(messages):
        if m.role != "user":
            continue
        if not isinstance(m.content, list):
            return out
        for part in m.content:
            if getattr(part, "type", None) != "image_url":
                continue
            url = getattr(getattr(part, "image_url", None), "url", "") or ""
            if url.startswith("data:") and ";base64," in url:
                try:
                    b64 = url.split(";base64,", 1)[1]
                    out.append(base64.b64decode(b64))
                except Exception:
                    continue
            if len(out) >= cap:
                break
        return out
    return out


# R8.E 之前的老接口——只返回第一张图。保留是为了 grep 兼容,新代码都用 _extract_image_bytes_list。
# 一旦确认没有别的模块 import 这个函数,就可以删。
def _extract_image_bytes(messages) -> bytes | None:
    """Legacy single-image accessor — kept for paths that haven't migrated
    to the list form. Returns the FIRST image only."""
    imgs = _extract_image_bytes_list(messages, cap=1)
    return imgs[0] if imgs else None


# Bug 2 修复(R8.F,Sam):把 iPhone 12MP 原图(~4032×3024)缩到 1024px 长边再交给视觉 LLM。
# **实测降幅**(见 tools/bench_image_downscale.py,3 张图请求):
#   * 网络 payload:1.82 MB → 156 KB   (11.7× 缩,真实节省网络 + base64 解码时间)
#   * Vision LLM tokens:7,377 → 3,147 (2.3× 省,因为 Anthropic 服务端本来就有
#                                      1568px cap,12× 像素比经过 cap 之后只放大成 2.3× token 差)
#   * 服务端 CPU 开销:+203 ms        (PIL LANCZOS × 3,不到节省时间的 1%)
# 预计端到端:30 s → ~13 s。1024px 不影响品牌/品类识别精度
# (Anthropic 官方推荐"1.15 MP 以下",1024×768 ≈ 0.79 MP,在最佳区间)。
# **CLIP 检索仍用原图字节**(img_bytes_list[0]),也用原图字节算 cache key,所以视觉检索精度完全不变。
def _downscale_image_data_url(url: str, max_edge: int = 1024) -> str:
    """Resize a base64 ``data:image/...`` URL so the longer side is ``max_edge``.

    Returns the input unchanged on any of:
      * non-data URL (remote https — let the LLM fetch its own),
      * image already ≤ ``max_edge`` on both sides,
      * decode / encode error (we never want to drop a request over a resize).
    """
    import base64
    import io

    if not url.startswith("data:") or ";base64," not in url:
        return url
    try:
        from PIL import Image
    except Exception:  # PIL missing — degrade gracefully, send original.
        return url
    try:
        _mime_header, b64 = url.split(";base64,", 1)
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw))
        img.load()
        w, h = img.size
        if max(w, h) <= max_edge:
            return url
        if w >= h:
            new_w, new_h = max_edge, max(1, int(h * max_edge / w))
        else:
            new_w, new_h = max(1, int(w * max_edge / h)), max_edge
        img = img.convert("RGB").resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        log.debug(f"downscale: {w}×{h} → {new_w}×{new_h} ({len(raw)} → {buf.tell()} bytes)")
        return f"data:image/jpeg;base64,{new_b64}"
    except Exception as e:  # noqa: BLE001
        log.warning(f"image downscale failed, sending original: {e}")
        return url


# 遍历 content list,对每个 image_url 调一次 _downscale_image_data_url。
# 纯文字 content(content 是 str 而不是 list)原样返回——这条只在多模态路径触发。
# 保留 dict 浅拷贝,避免改原始 req 对象(req 可能还要被 cache key 序列化)。
def _downscale_message_content(content, max_edge: int = 1024):
    """Walk a list-form chat content payload and downscale any image_url
    data URLs. String / non-list content passes through unchanged so the
    text-only paths are untouched. Used right before handoff to the LLM.
    """
    if not isinstance(content, list):
        return content
    out = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            iu = part.get("image_url") or {}
            url = iu.get("url", "")
            new_url = _downscale_image_data_url(url, max_edge=max_edge)
            if new_url is url:
                out.append(part)
            else:
                new_part = dict(part)
                new_part["image_url"] = {**iu, "url": new_url}
                out.append(new_part)
        else:
            out.append(part)
    return out

# 调 LLM 的流式接口,初次错就指数退避重试(0.5s → 1s → 2s),最多 3 次。
# **关键设计**:重试只能在**还没 yield 出第一个 delta 之前**发生
# (连接被拒、上游 429 / 5xx、SSL 握手挂)。一旦开始往外吐 token,中途断了就直接抛——
# 因为如果"半重试"再吐一次,用户会看到回答出现两遍,体验灾难。
# 这是流式 API 跟普通 retry 装饰器的差别。
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


# 主路由:POST /chat/stream → SSE 流式响应。**整个后端最重要的函数**。
# 流程见文件顶部 docstring 的 7 步。本函数把这 7 步串起来:
#   503 ready 检查 → 提取文本/图片 → 检索(CLIP or hybrid+rerank) →
#   detect cart 意图 → 算 cache key → 构造 history → 嵌套 generator 流式吐 SSE。
# 返回 StreamingResponse 走 SSE。LLM 还没产生 token 时 HTTP 头已经发出去了,
# 这样 iOS 端能"立即"知道请求被接受。
@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    # /ready 门控:Tujie 加的 readiness 检查。bge / cross-encoder / BM25 没预热完之前
    # 返回 503,iOS 收到后可以重试,不会拿到一个超慢的首条请求。
    if not getattr(request.app.state, "retrieval_ready", False):
        raise HTTPException(status_code=503, detail="retrieval is warming up; retry after /ready returns ready")

    t_received = time.perf_counter()
    user_text = _extract_user_text(req.messages)
    retrieval_query = build_retrieval_query(req.messages)

    # Retrieval ----------------------------------------------------------------
    products: list[dict] = []
    img_bytes_list: list[bytes] = []
    if _has_image(req.messages):
        img_bytes_list = _extract_image_bytes_list(req.messages)
        # CLIP retriever is single-image; use the first attachment as the
        # visual query. The LLM still sees all images via the content array.
        if img_bytes_list:
            products = top_k_image(img_bytes_list[0], k=3)
    if not products:
        explicit_filters = req.filters.model_dump(exclude_none=True) if req.filters else None
        conversation_filter = build_conversation_filter(req.messages, explicit_filters)
        products = top_k(
            retrieval_query,
            k=5,
            filters=explicit_filters,
            conversation_filter=conversation_filter,
            intent_text=user_text,
        )
    products = await asyncio.to_thread(normalize_product_prices, products)
    t_retrieval = time.perf_counter()

    # Cart-intent detection ----------------------------------------------------
    cart_event = _detect_cart_intent(user_text)

    # Cache --------------------------------------------------------------------
    # R8.E: multi-attachment messages now hash the SORTED concat of all
    # image SHAs so the cache key is order-invariant and bounded.
    cache_key = make_key(
        system_prompt=f"{_PROMPT[:128]}|fx={pricing_cache_token(products)}",
        messages_json=req.model_dump_json(),
        image_sha=hash_image_bytes_list(img_bytes_list),
    )
    cached_events = cache.get(cache_key)

    # Build messages + provider ------------------------------------------------
    system = _PROMPT.format(catalog=_build_catalog(products))
    if _has_image(req.messages):
        last_user = next((m for m in reversed(req.messages) if m.role == "user"), None)
        user_content = last_user.model_dump()["content"] if last_user else user_text
        # Bug 2 fix (R8.F): downscale full-res iPhone photos to 1024px before
        # the vision-LLM call. Token cost is ~12× lower for the typical 12MP
        # iPhone upload, which is what drove 3-image requests past 30s.
        user_content = _downscale_message_content(user_content, max_edge=1024)
        history = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    else:
        history = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
    provider = get_provider()

    # 嵌套 async generator:实际产出 SSE 流的地方。
    # 为什么用 nested:它需要闭包访问外层的 cache_key / cached_events / history / products,
    # 写成嵌套比传一堆参数清爽。
    # 三种 path:
    #   (1) cart 意图:第一时间推 cart_intent 事件,让 iOS UI 立刻响应
    #   (2) cache hit:回放历史事件,加 15ms 间隔模拟打字感(不然瞬间整段刷出来体验差)
    #   (3) cache miss:retry 调 LLM,流式 yield delta、推 product_card、最后写回 cache。
    # 整个 generator 全程监听 request.is_disconnected(),客户端关页面立刻退出,不再烧 LLM。
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


# 一次请求结束时打一行结构化 JSON 日志。
# 字段:cache hit/miss、检索耗时、首 token 耗时、总耗时、query 前 60 字预览。
# 用 print 而不是 log.info 是因为 uvicorn 默认不把 named logger 的 INFO 推到 stdout,
# 而 print 一定能走进 access log 同一通道,运维抓日志只看一个文件就够,不用配 handler。
# 答辩可讲:JSON 行容易 grep、容易喂给后端日志工具(比如 Loki / Datadog)做监控。
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
