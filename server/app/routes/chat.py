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
from urllib.parse import quote

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
    # R9.B — anonymous per-device id (iOS identifierForVendor). When
    # present, the retrieval layer applies this user's 👍/👎 preference
    # prior. Optional: omitting it just disables personalization for
    # that request (pure relevance).
    user_id: str | None = Field(default=None)


# 把字典编码成 SSE(Server-Sent Events)标准格式:`data: {json}\n\n`。
# SSE 协议要求每条事件以 "data: " 开头、双换行结尾。iOS 端的 URLSession.bytes.lines
# 按这个分隔符切流,所以这个格式是"协议",不能改。ensure_ascii=False 让中文不变成 \uXXXX。
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# Tightened system prompt: commits on visual matches, structured filtering rule.
# R9.A.3 — 加入「来源标签」规则: LLM 每条事实陈述附加 [目录✓] 或 [推断?]
# 标签,iOS 端解析后渲染成绿/琥珀色小徽章。让用户(评委)一眼分得清
# 哪些信息来自我们的商品目录、哪些是 LLM 推断 — 反 Rufus-style 谄媚式
# 报错的核心差异化。
_PROMPT = (
    "你是一名中文电商导购助手。仅基于下面的商品目录回答；目录中没有的商品、价格、优惠绝对不要编造。\n"
    "\n"
    "规则：\n"
    "1. 如果用户上传了图片，你的视觉识别（品牌+品类）若能匹配目录中的某款商品，"
    "**直接确认这就是该商品**，不要含糊地说『目录中没有』。\n"
    "2. 如果用户说『不要 X』『除了 X』『不含 X』，从候选集中**剔除**含 X 的商品。\n"
    "3. 多商品对比：从『价格、关键成分、适用场景、优劣势』中选择 3-5 个维度，逐条列出。\n"
    "4. 回复格式：先一句话推荐，再 2-4 条理由（每条 ≤ 30 字），最后可选一句替代建议。\n"
    "5. **来源标签**: 每条事实陈述紧跟一个标签:\n"
    "   - `[目录✓]` 如果该信息明确来自下面的商品目录(价格/品牌/标题/marketing_description 里写到的内容);\n"
    "   - `[推断?]` 如果该信息是基于常识或类似商品推断的(如使用感受/适用场景/化学成分细节);\n"
    "   标签放在该句末尾、句号之前。不要给标题/品牌名加标签,只给\"事实陈述\"加。\n"
    "   示例: \"珊珂洗颜专科 ¥52[目录✓], 适合敏感肌[推断?]。\"\n"
    "\n## 商品目录\n{catalog}\n"
)


# R10 #5 — 主动反问 (proactive clarification) 的 system prompt。
# 当后端判定用户需求「信息不足」时,用这个 prompt 代替 _PROMPT:
# 让 LLM **反问澄清**而不是硬推商品,本轮也不检索、不出商品卡。
_CLARIFY_PROMPT = (
    "你是一名中文电商导购助手。用户当前的需求**信息不足**,直接推荐会答非所问。\n"
    "请用**一到两句**自然、口语化的话**反问澄清**,引导用户补充这些关键信息:{dimensions}。\n"
    "要求:不要用编号罗列;**不要推荐任何具体商品、品牌或价格**;结尾可给一个简短示例帮用户理解怎么回答。"
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
        # R10 fix — the seed paths contain Chinese category folders
        # (e.g. "1_美妆护肤/images/..."). Emit them percent-encoded so the
        # iOS client's URL(string:) builds a valid URL every time;
        # un-encoded non-ASCII made AsyncImage fail intermittently. quote()
        # keeps the "/" separators; FastAPI StaticFiles decodes on serve.
        return f"/static/{quote(image_path)}"
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
    # R9.A.2 — surface retrieval signals on the iOS "why this is recommended"
    # debug card. The `_retrieval` dict is attached upstream in rag_client +
    # rerank; the chat route picks only the user-facing fields so we don't
    # leak internal-only fields like `query`.
    retrieval_signals: dict | None = None
    raw_retrieval = p.get("_retrieval")
    if isinstance(raw_retrieval, dict):
        retrieval_signals = {
            "rrf_score": raw_retrieval.get("rrf_score"),
            "dense_rank": raw_retrieval.get("dense_rank"),
            "bm25_rank": raw_retrieval.get("bm25_rank"),
            "rerank_score": raw_retrieval.get("rerank_score"),
            "rerank_rank": raw_retrieval.get("rerank_rank"),
            "rerank_model": raw_retrieval.get("rerank_model"),
        }
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
            "retrieval_signals": retrieval_signals,
        },
    }


# Intent detection for 4.1 cart flow.
_ADD_TO_CART = re.compile(r"加入?购物?车|加购|加入车|放购物?车")
_CHECKOUT = re.compile(r"下单|结(账|算)|去结算|帮我下个?单|买单")

# R9.A.5 — comparison-intent detector (proposal #10). When the user asks
# to compare two specific products ("A 和 B 哪个更好 / vs / 对比"), we
# nudge the LLM to emit a structured markdown table — much clearer than
# a paragraph for side-by-side decisions.
_COMPARISON_INTENT = re.compile(r"vs\.?|哪个(?:更|比较)|对比|比一?比|比较一?下|甲乙|哪款")

# R9.A.5 — scene/intent detector (proposal #9, scene builder). When the
# user names a scenario rather than a category, we steer the LLM to pick
# 3-4 COMPLEMENTARY products across multiple categories instead of 3
# variants of one product. Examples that hit:
#   "露营要带的东西" → 防晒 + 户外鞋 + 食品 + 充电宝
#   "母亲节送什么" → 护肤 + 数码 + 食品 + 服饰
#   "新生入学清单" → 笔电 + 书 + 日用品
_SCENE_KEYWORDS = (
    "露营", "健身", "新生", "入学", "母亲节", "父亲节", "情人节",
    "送礼", "送什么", "婚礼", "婚庆", "出差", "旅行", "野餐",
    "聚会", "派对", "搬家", "结婚", "宝宝", "礼物", "套装",
    # R10 — broaden scene coverage (rubric "三亚度假/搭配方案" example
    # missed because 度假/三亚/海岛 weren't here).
    "度假", "三亚", "海岛", "海边", "沙滩", "出游", "踏青", "爬山",
    "登山", "滑雪", "过年", "春节", "中秋", "国庆", "开学", "毕业",
    "约会", "面试", "通勤", "搭配", "一套", "方案", "清单", "周末",
)


def _is_comparison_query(text: str) -> bool:
    if not text:
        return False
    return _COMPARISON_INTENT.search(text) is not None


def _detect_scene(text: str) -> str | None:
    if not text:
        return None
    for kw in _SCENE_KEYWORDS:
        if kw in text:
            return kw
    return None


def _count_claim_markers(text: str) -> dict[str, int]:
    """R9.A.5 — count provenance markers emitted by the LLM (per
    proposal #8 fact-check). Returns {"verified": N, "inferred": M}.
    iOS renders a small footer under the assistant bubble showing how
    many claims of each kind the model produced."""
    if not text:
        return {"verified": 0, "inferred": 0}
    return {
        "verified": text.count("[目录✓]"),
        "inferred": text.count("[推断?]"),
    }

# R10 — conversational cart DELETE (rubric "删掉第二个"). Detects a remove
# intent plus an ordinal so iOS can drop the Nth cart line. The ordinal is
# 1-based (第一个=1); the client converts to a 0-based index. "最后一个"
# maps to a sentinel -1 the client interprets as "last".
_REMOVE_FROM_CART = re.compile(r"删(?:掉|除)?|去掉|移除|拿掉|不要(?!.*[?？])|清掉")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_RE = re.compile(r"第\s*([0-9一二两三四五六七八九十]+)\s*(?:个|件|款|项)?")
_LAST_RE = re.compile(r"最后(?:一)?(?:个|件|款)?")
# R10 #4.1⭐⭐ — conversational quantity set ("把数量改成2" / "第二个改成3个").
_SET_QTY_RE = re.compile(r"(?:改成|改为|设为|设成|调成|调为|换成|变成|要)\s*([0-9一二两三四五六七八九十]+)\s*(?:个|件|份|瓶|盒)?")
_QTY_KEYWORD_RE = re.compile(r"数量|数目|几个")


def _cn_to_int(token: str) -> int | None:
    """Parse an Arabic or Chinese numeral token to int (handles 十/十N/N十
    lightly — plenty for a shopping cart)."""
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + _CN_NUM.get(token[1:], 0)
    if token.endswith("十"):
        return _CN_NUM.get(token[:-1], 0) * 10
    return _CN_NUM.get(token)


def _parse_ordinal(text: str) -> int | None:
    """Return a 1-based ordinal from '第二个'/'第2个', -1 for '最后一个',
    or None if no ordinal is present."""
    if _LAST_RE.search(text):
        return -1
    m = _ORDINAL_RE.search(text)
    if not m:
        return None
    return _cn_to_int(m.group(1))


def _parse_set_quantity(text: str) -> tuple[int, int] | None:
    """Return (index, quantity) for a conversational quantity-set, or None.

    Fires ONLY when a set-verb+number is present AND the message carries a
    strong cart-quantity signal — either an explicit '数量' keyword or an
    ordinal (第N个). This keeps '我要2个面霜' (a new product search) from
    being misread as a quantity edit.

    index: 1-based ordinal of which cart line; -1 = last (default when no
    ordinal). quantity: the target count (>0).
    """
    m = _SET_QTY_RE.search(text)
    if not m:
        return None
    qty = _cn_to_int(m.group(1))
    if qty is None or qty <= 0:
        return None
    ordinal = _parse_ordinal(text)
    if ordinal is None and not _QTY_KEYWORD_RE.search(text):
        return None  # too ambiguous (e.g. "要2个面霜") — not a cart edit
    return (ordinal if ordinal is not None else -1, qty)


# 纯正则识别用户意图是不是「加购」「下单」或「删除」。
# 匹配到就立刻返回 cart_intent 事件,iOS 收到后**先于 LLM 回复**操作购物车 UI。
# 这是 4.1 题面要求的购物车流——意图识别**不走 LLM**(LLM 慢且不稳)。
# 优先级:checkout > remove > add(下单最强;删除带序数;加购兜底)。
def _detect_cart_intent(text: str) -> dict | None:
    if not text:
        return None
    if _CHECKOUT.search(text):
        return {"type": "cart_intent", "action": "checkout"}
    # Quantity set ("把数量改成2" / "第二个改成3个") — checked before remove
    # since both can mention an ordinal; the set-verb disambiguates.
    sq = _parse_set_quantity(text)
    if sq is not None:
        return {"type": "cart_intent", "action": "set_quantity", "index": sq[0], "quantity": sq[1]}
    # Remove only fires when there's BOTH a remove verb AND an ordinal /
    # "最后" — keeps "不要日系" (a negation filter, not a cart op) from
    # being misread as a delete.
    if _REMOVE_FROM_CART.search(text):
        ordinal = _parse_ordinal(text)
        if ordinal is not None:
            return {"type": "cart_intent", "action": "remove", "index": ordinal}
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


# R10 #5 — 主动反问 (proactive clarification) 检测。
# 只在「明显信息不足」的**首轮**模糊请求上触发(没有品类/品牌/价格/属性等任何
# 具体信号),非常保守——绝不在正常请求上反复追问、惹人烦。命中时本轮不检索、
# 不出商品卡,改用 _CLARIFY_PROMPT 让 LLM 反问。
_VAGUE_INTENT = re.compile(
    r"推荐(点|个|些|下|一下)?(什么|啥|东西|商品|礼物|好物|好东西)"
    r"|随便看看|看看有(什么|啥)"
    r"|有(什么|啥)(推荐|好(东西|物|货))"
    r"|帮我(挑|选|看看|推荐|参考)"
    r"|不知道(买|选|送)(什么|啥)|买点(什么|啥)|送(什么|啥)(礼物|好)"
)
_GIFT_HINT = re.compile(r"礼物|送(人|朋友|女友|男友|长辈|妈|爸|同事|领导|老师|闺蜜|对象)|送给")
# 明确的品类词——出现任意一个就说明请求已经够具体,不反问。
_CONCRETE_CATS = (
    "面霜", "防晒", "洁面", "洗面", "口红", "眼霜", "面膜", "精华", "水乳", "粉底",
    "耳机", "手机", "笔记本", "电脑", "平板", "相机", "手表", "键盘", "鼠标", "音箱",
    "跑鞋", "运动鞋", "板鞋", "篮球鞋", "羽绒服", "卫衣", "外套", "裤", "背包", "行李箱",
    "零食", "奶粉", "纸尿", "牙膏", "沐浴", "洗发", "咖啡", "茶", "锅", "杯",
)


def _has_concrete_signal(text: str) -> bool:
    """请求里是否带了能检索的具体信号(品类 / 价格 / 品牌)。有就别反问。"""
    if any(c in text for c in _CONCRETE_CATS):
        return True
    if re.search(r"\d+\s*(元|块|rmb|￥|¥)|预算|以内|左右|便宜|平价|高端|性价比|划算", text, re.I):
        return True
    try:
        from rag.retrieve.brand_origin import BRAND_ORIGIN
        tl = text.lower()
        if any(len(b) >= 2 and b.lower() in tl for b in BRAND_ORIGIN):
            return True
    except Exception:
        pass
    return False


def _needs_clarification(text: str, messages) -> str | None:
    """需求太模糊、无法检索时,返回「该追问哪些维度」的提示串;否则 None。
    保守策略:有图(图本身就是意图)、多轮(上文已有约束)、或带具体信号 → 不反问。"""
    if not text:
        return None
    if _has_image(messages):
        return None
    # 多轮里上文通常已经给了约束,别打断——只在首轮模糊请求上反问。
    try:
        user_turns = sum(1 for m in messages if getattr(m, "role", None) == "user")
    except TypeError:
        user_turns = 1
    if user_turns > 1:
        return None
    is_gift = bool(_GIFT_HINT.search(text))
    if not (_VAGUE_INTENT.search(text) or is_gift):
        return None
    # A concrete signal (品类 / 预算 / 品牌) means we can already retrieve —
    # e.g. "送朋友耳机" or "送女友 300 元的口红" should recommend, not ask.
    if _has_concrete_signal(text):
        return None
    if is_gift:
        return "送礼对象(性别 / 年龄 / 和你的关系)、预算大概多少、什么场合用"
    return "想找哪类商品(比如美妆护肤 / 数码 / 服饰 / 食品)、预算范围、有没有偏好的品牌或风格"


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
    # R11.fix — empty/whitespace input with no image: skip retrieval AND the
    # LLM. An empty user message makes the provider 400 (and leaks the raw
    # upstream error to the client) while retrieval returns random default
    # cards. Answered with a canned clarify in the generator below.
    empty_query = (not user_text.strip()) and not _has_image(req.messages)

    # Retrieval ----------------------------------------------------------------
    # R8.E.3: top_k* are sync (torch / sentence-transformers under the hood).
    # Running them directly on the FastAPI event loop blocks ALL other
    # requests for the duration — that's why /cache/stats times out during
    # an in-flight /chat/stream. asyncio.to_thread offloads to the default
    # thread pool so the event loop stays responsive (matches the already-
    # offloaded normalize_product_prices call below).
    products: list[dict] = []
    img_bytes_list: list[bytes] = []
    # R10 #5 — 主动反问: if the request is too vague to recommend, skip
    # retrieval entirely (no product cards) and let the LLM ask a clarifying
    # question via _CLARIFY_PROMPT below.
    clarify_dims = _needs_clarification(user_text, req.messages)
    # R10 #5 — tappable quick-reply chips for the clarification turn. Each
    # chip is a ready-made next message; tapping it sends concrete signal
    # (品类/预算/对象) so the FOLLOW-UP turn retrieves normally.
    clarify_chips: list[str] = []
    if clarify_dims:
        if _GIFT_HINT.search(user_text):
            clarify_chips = ["送女友", "送男友", "送长辈", "送朋友",
                             "预算 300 以内", "预算 300-800", "生日礼物"]
        else:
            clarify_chips = ["美妆护肤", "数码电子", "服饰运动", "食品零食",
                             "500 元以内", "1000 左右"]
    if clarify_dims is None and not empty_query:
        if _has_image(req.messages):
            img_bytes_list = _extract_image_bytes_list(req.messages)
            # CLIP retriever is single-image; use the first attachment as the
            # visual query. The LLM still sees all images via the content array.
            if img_bytes_list:
                products = await asyncio.to_thread(top_k_image, img_bytes_list[0], k=3)
        if not products:
            explicit_filters = req.filters.model_dump(exclude_none=True) if req.filters else None
            conversation_filter = build_conversation_filter(req.messages, explicit_filters)
            products = await asyncio.to_thread(
                top_k,
                retrieval_query,
                k=5,
                filters=explicit_filters,
                conversation_filter=conversation_filter,
                intent_text=user_text,
                user_id=req.user_id,
            )
        products = await asyncio.to_thread(normalize_product_prices, products)
    t_retrieval = time.perf_counter()

    # Cart-intent detection ----------------------------------------------------
    # A clarification turn is never a cart op — skip detection so a vague
    # "帮我挑个东西" can't be misread.
    cart_event = None if clarify_dims else _detect_cart_intent(user_text)

    # Cache --------------------------------------------------------------------
    # R8.E: multi-attachment messages now hash the SORTED concat of all
    # image SHAs so the cache key is order-invariant and bounded.
    # R9.B-FIX: fold the user's CURRENT preference state into the key. The
    # preference prior (proposal #12) re-orders products inside top_k, but
    # the response cache replays stored events on a hit — so without this,
    # tapping 👍/👎 and re-asking the SAME query would replay the stale
    # (pre-preference) order. Hashing the weights means any change to the
    # user's preferences busts the cache for that user and a fresh,
    # re-ranked response is generated. Empty for users with no taps, so
    # cross-user caching is unaffected.
    pref_token = ""
    if req.user_id:
        try:
            from app.services.preferences_db import get_weights as _get_weights
            _w = await asyncio.to_thread(_get_weights, req.user_id)
            if _w:
                pref_token = json.dumps(_w, sort_keys=True, ensure_ascii=False)
        except Exception:
            pref_token = ""
    cache_key = make_key(
        system_prompt=f"{_PROMPT[:128]}|fx={pricing_cache_token(products)}|pref={pref_token}",
        messages_json=req.model_dump_json(),
        image_sha=hash_image_bytes_list(img_bytes_list),
    )
    cached_events = cache.get(cache_key)

    # Build messages + provider ------------------------------------------------
    # R9.A.5 — query-shape addendums to the system prompt.
    #   * Comparison intent: nudge the LLM to emit a markdown table for
    #     side-by-side answers (proposal #10).
    #   * Scene query: instruct the LLM to pick complementary products
    #     across categories instead of three variants of one product
    #     (proposal #9 scene builder).
    addendum = ""
    if _is_comparison_query(user_text):
        addendum += (
            "\n\n6. **本轮是商品对比**: 用 Markdown 表格输出对比 — "
            "表头一行,每个商品一列,行包括「价格」「主要特点」「适用场景」「优劣势」。"
            "示例:\n"
            "| 维度 | 商品A | 商品B |\n"
            "| --- | --- | --- |\n"
            "| 价格 | ¥720 | ¥760 |\n"
            "| 适用场景 | 熬夜修护 | 日常稳肌 |\n"
        )
    scene_kw = _detect_scene(user_text)
    if scene_kw:
        addendum += (
            f"\n\n7. **本轮是场景搭配 ({scene_kw})**: 不要推同类目 3 个,而是从目录里挑 "
            "3-4 件互补的商品凑成一套(尽量来自不同类目, 比如食品+数码+服饰)。每件用 "
            "[目录✓] 标注价格/品牌,简短说明该件在该场景中的用途。"
        )
    # R10 #5 — on a clarification turn, swap in the 反问 prompt (no catalog,
    # no comparison/scene addendum) so the LLM asks instead of recommending.
    if clarify_dims:
        system = _CLARIFY_PROMPT.format(dimensions=clarify_dims)
    else:
        system = _PROMPT.format(catalog=_build_catalog(products)) + addendum
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
        # R11.fix — emit LIVE only, do NOT append to events_to_cache: a cached
        # cart_intent would replay ON TOP of this live one on a cache HIT, so
        # the client saw it twice (double add / remove / checkout). It's cheap
        # and deterministic from the query, so live-every-time is correct.
        if cart_event:
            yield _sse(cart_event)

        # R10 #5 — clarification chips, emitted early so iOS can render the
        # tappable quick-replies alongside the 反问 question. Same as
        # cart_event: emit live only, never cache (else double-emit on hit).
        if clarify_chips:
            yield _sse({"type": "clarify", "chips": clarify_chips})

        # R11.fix — empty/whitespace input: canned clarify, no LLM call (the
        # provider 400s on empty content). Single clarify + a friendly nudge.
        if empty_query:
            yield _sse({"type": "clarify",
                        "chips": ["推荐保湿面霜", "推荐运动鞋", "推荐降噪耳机", "推荐零食"]})
            yield _sse({"type": "delta",
                        "text": "你想找点什么呢?直接说品类、预算或场景就行,"
                                "比如「五百元以内的降噪耳机」。"})
            yield _sse({"type": "done"})
            return

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

        # R10 #4.4⭐⭐ — 首屏极速响应 (pipeline ordering). Emit the product
        # cards FIRST, before the LLM text stream. `products` is already the
        # fully-reranked production set (computed before this generator), so
        # this is a pure REORDER — no quality change, and the LLM is still
        # grounded on the exact same set. Effect: the user sees the goods as
        # soon as retrieval finishes (~0.3s cache-hit / sub-1s warm) instead
        # of waiting out the whole LLM generation for the cards to appear.
        for p in products:
            if await request.is_disconnected():
                return
            ev = _product_card_event(p)
            yield _sse(ev)
            events_to_cache.append(ev)

        # Cache miss: stream from LLM with retry/backoff.
        assistant_text_chunks: list[str] = []
        try:
            async for delta in _stream_chat_with_retry(provider, history):
                if await request.is_disconnected():
                    log.info("client disconnected mid-stream; cancelling")
                    return
                ev = {"type": "delta", "text": delta}
                yield _sse(ev)
                events_to_cache.append(ev)
                assistant_text_chunks.append(delta)
                if t_first_delta is None:
                    t_first_delta = time.perf_counter()
        except Exception as e:  # noqa: BLE001
            err = {"type": "error", "message": str(e), "code": "UPSTREAM"}
            yield _sse(err)
            events_to_cache.append(err)

        # R9.A.5 — proposal #8 fact-check: count provenance markers in
        # the assistant's reply. iOS renders a small footer
        # "✓ N 条已验证 · ? M 条推断" under the message bubble so the
        # user sees claim-level transparency without expanding anything.
        full_text = "".join(assistant_text_chunks)
        marker_counts = _count_claim_markers(full_text)
        if marker_counts["verified"] or marker_counts["inferred"]:
            claim_ev = {"type": "claim_summary", **marker_counts}
            yield _sse(claim_ev)
            events_to_cache.append(claim_ev)

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
