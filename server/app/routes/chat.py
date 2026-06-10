"""聊天流式接口。

处理流程:
  1. 提取最后一条用户文本 + 可选的图片字节。
  2. 检索 top 候选商品(带图走 CLIP;否则走 hybrid+rerank)。
  3. 构建商品目录文本 + system prompt;组装 messages。
  4. 缓存检查(对 system + messages + 图片 sha 做哈希)→ 命中则回放。
  5. 调用 LLM provider 流式生成,上游出错时按退避策略重试。
  6. 发送商品卡 + 意图事件;以 `done` 事件收尾。
  7. 打结构化耗时日志(received → retrieval → first delta → done)。

取消机制:客户端中途断开连接时,立即停止调用 LLM 并退出 generator,
避免白白烧掉配额。
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
from app.services.contextual_query import build_retrieval_query, _reorder_negation_object, message_text
from app.services.currency import normalize_product_prices, pricing_cache_token
# 这里能直接 import rag:上面 import rag_client 时
# 已经把仓库根目录放进了 sys.path。
from rag.retrieve.english_terms import augment_english_query, looks_english

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger("chat")


# Pydantic 请求体 schema。
# 客户端 POST /chat/stream 时的 JSON 必须长这样:messages 是完整对话历史(包含本轮),
# filters 是 iOS Settings 里设置的硬过滤(类目/品牌/价格区间,可选)。
# Pydantic 自动验证类型 + 字段名,不符合的 422 直接拒绝,不会进到我们写的业务代码。
class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: ChatFilters | None = Field(default=None)
    # R9.B — 匿名的设备级 id(iOS identifierForVendor)。携带时,
    # 检索层会叠加该用户的 👍/👎 偏好先验(preference prior)。
    # 可选字段:不传只是关闭本次请求的个性化
    # (退化为纯相关性排序)。
    user_id: str | None = Field(default=None)
    # R12 — UI 语言("zh"/"en")。决定助手的回复语言:英文模式用户会得到
    # 英文回答 + [catalog✓]/[inferred?] 来源标签。
    language: str | None = Field(default=None)


# 把字典编码成 SSE(Server-Sent Events)标准格式:`data: {json}\n\n`。
# SSE 协议要求每条事件以 "data: " 开头、双换行结尾。iOS 端的 URLSession.bytes.lines
# 按这个分隔符切流,所以这个格式是"协议",不能改。ensure_ascii=False 让中文不变成 \uXXXX。
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# 收紧后的 system prompt:视觉匹配到目录商品时果断确认,并带结构化的过滤规则。
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
    "\n## 必须遵守的纪律(最高优先级)\n"
    "- **每一轮独立判断**:历史轮说过『没有X』只代表那一轮的检索;本轮只看本轮的『商品目录』。"
    "只要本轮目录里有商品,就照常正面推荐,**绝不要**因为前几轮说过『没有』就惯性地继续说没有。"
    "候选之外的品类(如跑鞋/护具)是否在售你**无法判断**,不要断言『商店没有跑鞋』这类话——"
    "想引导时说『想看跑鞋的话直接说「推荐跑鞋」』即可。\n"
    "- 下面的『商品目录』是为本次查询**检索到的候选**,代表检索结果、不是整个商店。"
    "**绝对禁止**出现『商品目录为空』『目录里只有这几类』『目录商品有限/只有一款』"
    "『请上传或导入目录』这类说法,也不要建议用户去淘宝/京东/小红书/抖音等其它平台或品牌官网下单。\n"
    "- 若候选里没有完全符合用户(预算/品类/品牌)的商品:明确说明『没有完全符合的X』,"
    "再把候选里**最接近**的当作替代推荐(例如最接近预算的几款),照常给推荐和理由,不要空手而归。\n"
    "- 购物车由系统在你之外自动处理:真正的加购/下单/删除指令**不会进入本对话**,"
    "所以**本轮一定不是购物车操作**。绝不要声称『已加入购物车』『已为你下单/结算』『已删除』"
    "等任何购物车动作(系统没有做,这是虚假确认);也不要说自己无法操作购物车。"
    "想引导购买时,告诉用户直接说『加入购物车』即可。\n"
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
    """提供给 LLM 的价格文案,与 iOS 商品卡的显示保持一致。"""
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
    """解析商品应显示的图片 URL。

    优先级:
      1. data/seed/<cat>/images/ 下的本地 `image_path` —— 最可靠,
         由 FastAPI 经 /static/ 路由提供。AI-gen 种子商品用它,
         带 AI 渲染占位图的真品条目也用它
         (见 tools/generate_product_images.py)。
      2. 绝对外链 `image_url_external`(Amazon/JD CDN)—— 仅在没有本地图时
         使用。真品的外链经常 404,因为调研 agent 推断出的图片哈希
         无法和线上页面逐一核实。
    """
    image_path = p.get("image_path")
    if image_path:
        # R10 修复 — 种子数据的路径里有中文类目文件夹(如 "1_美妆护肤/images/...")。
        # 输出前先做百分号编码,保证 iOS 端 URL(string:) 每次都能构造出合法 URL;
        # 未编码的非 ASCII 字符曾让 AsyncImage 间歇性加载失败。quote()
        # 会保留 "/" 分隔符;FastAPI StaticFiles 响应时自动解码。
        return f"/static/{quote(image_path)}"
    ext = p.get("image_url_external")
    if ext and isinstance(ext, str) and ext.startswith(("http://", "https://")):
        return ext
    return None


# 给没有显式 `provenance` 块的 AI-gen 种子商品兜底的默认溯源信息。
# 透传到 iOS 后,卡片据此渲染"演示"徽章。
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
    """从商品 JSON 读取 provenance 溯源块;缺失时回退到 AI-gen 标记。"""
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
    # R9.A.2 — 把检索信号透出到 iOS 的"为什么推荐它"调试卡片上。
    # `_retrieval` 字典由上游的 rag_client + rerank 附加;
    # chat 路由这里只挑面向用户的字段,
    # 避免把 `query` 这类仅供内部使用的字段泄漏出去。
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


# 4.1 购物车流程的意图识别。
# R13 — 补上英文表达(线上探针发现 "add this to my cart" 漏到了检索路径;
# 英文固定话术 + 语言检测早就有了,
# 只是这几个正则之前只认中文)。
_ADD_TO_CART = re.compile(
    r"加入?购物?车|加购|加入车|放购物?车"
    r"|\badd\b[^.!?，。]{0,30}\bto\s+(?:my\s+|the\s+)?(?:cart|bag)\b",
    re.IGNORECASE,
)
# "买单(?!反)" 是为了让 "买单反相机"/"买单反"(指单反相机)不被误判成
# "买单"(结账)这个下单动词。英文 "check out" 不能匹配"看看"义
# ("check out these headphones")—— 所以屏蔽后面紧跟冠词/指示代词的
# 名词短语。
_CHECKOUT = re.compile(
    r"下单|结(账|算)|去结算|帮我下个?单|买单(?!反)"
    r"|\bcheck\s*out\b(?!\s+(?:the|this|that|these|those|some|my|our|other))"
    r"|\bplace\s+(?:my|the|an)?\s*order\b|\bbuy\s+now\b|\bpay\s+now\b",
    re.IGNORECASE,
)
# 被否定的下单("先不要下单" / "don't check out yet")是用户在**拒绝**下单——
# 绝不能触发 checkout。用 .search() 在全句任意位置找否定词;
# 下单动词集合与 _CHECKOUT 保持一致。
_NEG_CHECKOUT = re.compile(
    r"(?:先不|先别|暂不|暂时不|还不|不用|不想|没|别|不)(?:要|想|用|着急|急着|现在|马上)?"
    r"(?:下单|结(?:账|算)|去结算|买单)"
    r"|(?:don'?t|do\s+not|not\s+(?:yet|now|going\s+to)|hold\s+off(?:\s+on)?|no\s+need\s+to|let'?s\s+not)"
    r"[^,.!?]{0,24}(?:check\s*out|checkout|order|pay)",
    re.IGNORECASE,
)
# R11.demo-fix — 对话式清空购物车("把购物车清空" / "全部删掉")。
# 和删除路径(需要序数词)不同:清空是删掉**全部**条目,
# 所以绝不能要求出现"第N个"。iOS 把 action=clear 映射为 cart.clear()。
_CLEAR_CART = re.compile(
    r"清空|全部删(?:掉|除|了)|都删(?:掉|了)|删光|全部(?:移除|清掉)|清掉购物车|全清"
    r"|\b(?:clear|empty)\s+(?:my\s+|the\s+)?(?:cart|bag)\b",
    re.IGNORECASE,
)

# R9.A.5 — 对比意图检测器(提案 #10)。用户要求对比具体商品时
# ("A 和 B 哪个更好 / vs / 对比"),引导 LLM 输出结构化的
# markdown 表格 —— 做并排决策时,
# 表格比一段文字清楚得多。
_COMPARISON_INTENT = re.compile(r"vs\.?|哪个(?:更|比较)|对比|比一?比|比较一?下|甲乙|哪款")

# R9.A.5 — 场景意图检测器(提案 #9,场景搭配 scene builder)。
# 用户说的是场景而不是品类时,引导 LLM 跨多个类目挑 3-4 件
# **互补**的商品,而不是同一类商品的 3 个变体。
# 命中示例:
#   "露营要带的东西" → 防晒 + 户外鞋 + 食品 + 充电宝
#   "母亲节送什么" → 护肤 + 数码 + 食品 + 服饰
#   "新生入学清单" → 笔电 + 书 + 日用品
_SCENE_KEYWORDS = (
    "露营", "健身", "新生", "入学", "母亲节", "父亲节", "情人节",
    "送礼", "送什么", "婚礼", "婚庆", "出差", "旅行", "野餐",
    "聚会", "派对", "搬家", "结婚", "宝宝", "礼物", "套装",
    # R10 — 扩大场景覆盖(评分细则里的"三亚度假/搭配方案"示例
    # 之前漏检,因为 度假/三亚/海岛 不在这个列表里)。
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
    """R9.A.5 — 统计 LLM 输出里的来源标签数量(对应提案 #8 事实核查)。
    返回 {"verified": N, "inferred": M}。
    iOS 在助手气泡下方渲染一行小字 footer,
    展示模型产出的两类事实陈述各有多少条。"""
    if not text:
        return {"verified": 0, "inferred": 0}
    return {
        "verified": text.count("[目录✓]"),
        "inferred": text.count("[推断?]"),
    }

# R10 — 对话式删除购物车条目(评分细则示例"删掉第二个")。检测「删除意图 +
# 序数词」,iOS 据此删掉第 N 条购物车记录。序数从 1 开始(第一个=1);
# 客户端自行换算成 0 起始的下标。"最后一个"映射为哨兵值 -1,
# 客户端把它解释为"最后一条"。
# 明确无歧义的购物车删除动词(只要再带上序数词,就一定是购物车操作)。
_REMOVE_VERB = re.compile(r"删(?:掉|除)?|去掉|移除|拿掉|清掉")
# "不要"有歧义:"不要第二个"既可能是"删掉购物车第 2 条",也可能(在推荐流程里
# 远更常见)是"不想要第 N 个**检索结果**,换别的看看"。
# 只有出现明确的购物车上下文时,才把"不要 + 序数"当成购物车删除;
# 否则视为检索条件的细化,必须流向检索路径。
_REMOVE_NEG = re.compile(r"不要(?!.*[?？])")
_CART_CONTEXT = re.compile(r"购物车|车里|车中|购物袋|袋里")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_RE = re.compile(r"第\s*([0-9一二两三四五六七八九十]+)\s*(?:个|件|款|项)?")
_LAST_RE = re.compile(r"最后(?:一)?(?:个|件|款)?")
# R10 #4.1⭐⭐ — 对话式修改数量("把数量改成2" / "第二个改成3个")。
_SET_QTY_RE = re.compile(r"(?:改成|改为|设为|设成|调成|调为|换成|变成|要)\s*([0-9一二两三四五六七八九十]+)\s*(?:个|件|份|瓶|盒)?")
_QTY_KEYWORD_RE = re.compile(r"数量|数目|几个")


def _cn_to_int(token: str) -> int | None:
    """把阿拉伯或中文数字 token 解析成 int(对 十/十N/N十 做了轻量处理 ——
    对购物车场景绰绰有余)。"""
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
    """从 '第二个'/'第2个' 解析出 1 起始的序数;'最后一个' 返回 -1;
    没有序数词时返回 None。"""
    if _LAST_RE.search(text):
        return -1
    m = _ORDINAL_RE.search(text)
    if not m:
        return None
    return _cn_to_int(m.group(1))


def _parse_set_quantity(text: str) -> tuple[int, int] | None:
    """解析对话式修改数量,返回 (index, quantity);不是数量修改则返回 None。

    只有同时满足「修改动词 + 数字」且消息带有强购物车数量信号
    (显式的'数量'关键词,或序数词"第N个")时才触发。
    这样 '我要2个面霜'(其实是新的商品搜索)就不会被误判成
    数量修改。

    index:目标购物车条目的 1 起始序数;-1 表示最后一条
    (无序数词时的默认值)。quantity:目标数量(>0)。
    """
    m = _SET_QTY_RE.search(text)
    if not m:
        return None
    qty = _cn_to_int(m.group(1))
    if qty is None or qty <= 0:
        return None
    ordinal = _parse_ordinal(text)
    if ordinal is None and not _QTY_KEYWORD_RE.search(text):
        return None  # 太模糊(如 "要2个面霜")—— 不算购物车修改
    return (ordinal if ordinal is not None else -1, qty)


# 纯正则识别用户意图是不是「加购」「下单」或「删除」。
# 匹配到就立刻返回 cart_intent 事件,iOS 收到后**先于 LLM 回复**操作购物车 UI。
# 这是 4.1 题面要求的购物车流——意图识别**不走 LLM**(LLM 慢且不稳)。
# 优先级:checkout > remove > add(下单最强;删除带序数;加购兜底)。
def _detect_cart_intent(text: str) -> dict | None:
    if not text:
        return None
    if _CHECKOUT.search(text) and not _NEG_CHECKOUT.search(text):
        return {"type": "cart_intent", "action": "checkout"}
    # 清空整个购物车("把购物车清空")—— 放在删除之前判断,
    # 因为清空不带序数词,而删除必须带。
    if _CLEAR_CART.search(text):
        return {"type": "cart_intent", "action": "clear"}
    # 修改数量("把数量改成2" / "第二个改成3个")—— 先于删除判断,
    # 因为两者都可能带序数词;靠修改动词来消歧。
    sq = _parse_set_quantity(text)
    if sq is not None:
        return {"type": "cart_intent", "action": "set_quantity", "index": sq[0], "quantity": sq[1]}
    # 删除只有在**同时**出现删除动词和序数词/"最后"时才触发。
    # 显式删除动词(删/去掉/移除/...)没有歧义;有歧义的"不要 + 序数"
    # 还额外要求明确的购物车上下文,这样"不要第一个,换别的推荐"
    # (细化检索结果)才不会被误判成购物车删除、
    # 被短路掉而进不了检索。
    remove_signal = _REMOVE_VERB.search(text) or (
        _REMOVE_NEG.search(text) and _CART_CONTEXT.search(text)
    )
    if remove_signal:
        ordinal = _parse_ordinal(text)
        if ordinal is not None:
            return {"type": "cart_intent", "action": "remove", "index": ordinal}
    if _ADD_TO_CART.search(text):
        return {"type": "cart_intent", "action": "add"}
    return None

# R13 — 给 LLM 的多轮文本历史。此前 LLM 只看得到最后一条 user 消息,跟进轮
# (「1000以内」/「any cheaper ones?」)对 LLM 完全没有上下文 → 反问品类、或对着
# 卡片字面发挥(「所有男装T恤都在1000以内」)。检索层早就用整个 history 了,
# LLM 也要看到。只取文本(图片只随当前轮走视觉通道),每条截断、限最近几轮,
# 合并连续同角色消息并保证以 user 开头(Anthropic provider 要求严格交替)。
def _prior_text_turns(messages, *, limit: int = 8, max_chars: int = 600) -> list[dict]:
    last_user_index = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].role == "user"), None
    )
    if last_user_index is None:
        return []
    turns: list[dict] = []
    for m in messages[:last_user_index]:
        if m.role not in ("user", "assistant"):
            continue
        text = message_text(m)
        if not text or text == "(image-only query)":
            continue
        text = text[:max_chars]
        if turns and turns[-1]["role"] == m.role:
            turns[-1]["content"] += "\n" + text
        else:
            turns.append({"role": m.role, "content": text})
    turns = turns[-limit:]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


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
    """R8.E:返回最后一条 user 消息里**所有**内联图片的字节,而不只是第一张。
    iOS 现在最多发送 `Attachment.maxCount` 个 image_url part;
    视觉 LLM 原生支持多图。CLIP 检索仍然只用 imgs[0]
    (视觉检索器是单图的);完整列表通过 content 数组
    交给 LLM。

    上限为 `cap`,既限制 payload 大小,也与 iOS 端的限制对齐。
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
    """遗留的单图取值接口 —— 为还没迁移到列表形式的调用方保留。
    只返回第一张图。"""
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
    """把 base64 的 ``data:image/...`` URL 缩放到长边为 ``max_edge``。

    以下任一情况原样返回输入:
      * 不是 data URL(远程 https 外链 —— 让 LLM 自己去取),
      * 图片两边都已 ≤ ``max_edge``,
      * 解码/编码出错(绝不能因为一次缩放失败就丢掉整个请求)。
    """
    import base64
    import io

    if not url.startswith("data:") or ";base64," not in url:
        return url
    try:
        from PIL import Image
    except Exception:  # 缺少 PIL —— 优雅降级,直接发原图。
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
    """遍历 list 形式的聊天 content,对其中所有 image_url 的 data URL 做缩放。
    字符串/非 list 的 content 原样通过,保证纯文字路径完全不受影响。
    在交给 LLM 之前的最后一步调用。
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
    """异步迭代 provider.stream_chat,早期错误按指数退避重试。"""
    delay = 0.5
    for attempt in range(1, max_attempts + 1):
        yielded = False
        try:
            async for delta in provider.stream_chat(history):
                yielded = True
                yield delta
            return
        except Exception as e:  # noqa: BLE001
            # 一旦有 token 已经吐出去,重试会把整个回答重新流一遍,
            # 用户会看到两份 —— 所以只在**早期**错误(任何 delta 之前)时重试。
            # 流中途的失败原样向上抛,
            # 与上面 docstring 的约定一致。
            if yielded or attempt == max_attempts:
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
    # 带具体信号(品类 / 预算 / 品牌)说明已经能检索了 ——
    # 比如"送朋友耳机"、"送女友 300 元的口红"应该直接推荐,而不是反问。
    if _has_concrete_signal(text):
        return None
    if is_gift:
        return "送礼对象(性别 / 年龄 / 和你的关系)、预算大概多少、什么场合用"
    return "想找哪类商品(比如美妆护肤 / 数码 / 服饰 / 食品)、预算范围、有没有偏好的品牌或风格"


# R11.demo-fix — 超出经营范围(out-of-domain)检测器。目录只卖
# 美妆护肤/数码电子/服饰运动/食品/母婴/家居/图书 这些实体商品。问汽车/机票/
# 酒店/房产/天气/股票的查询根本没有真实匹配,但 hybrid 检索仍会返回 top-5
# (相关性很低的随机卡)。命中时压掉商品卡(products=[]),
# 并让 LLM 礼貌拒答 —— 不再出答非所问的随机卡。
# 只收录多字词组(不收单字 车/房),避免 厨房/婴儿车 这类误命中。
_OUT_OF_DOMAIN = re.compile(
    r"汽车|买辆?车|一辆车|租车|车险|机票|订票|订机票|航班|高铁票|火车票|船票|"
    r"酒店|订房|民宿|房子|房产|买房|卖房|楼盘|租房|户型|"
    r"天气|气温|下雨|股票|基金|彩票|挂号|看病|外卖|点餐|打车|约车|加油站|"
    r"贷款|签证|护照|话费充值|游戏点卡|演唱会门票"
)


def _is_out_of_domain(text: str) -> bool:
    """查询属于目录无法提供的服务/品类、且不带任何目录内具体信号时返回 True。
    策略保守:只要查询同时提到了真实品类(如 '买车载手机支架'),
    商品卡照常保留。"""
    if not text:
        return False
    if not _OUT_OF_DOMAIN.search(text):
        return False
    # 用户如果**同时**提到了目录里真实的品类/品牌/预算,就不压制 ——
    # 他们多半是想要那个目录商品(比如 车载耳机)。
    if _has_concrete_signal(text):
        return False
    return True


# R11.demo-fix — 从查询里剥掉价格/预算措辞,让"预算筛空"后的**兜底**检索
# 忽略价格上限、重新检索同一**品类**(top_k 会重新解析查询文本,
# 既提取硬价格过滤又提取价格意图层,所以仅仅丢掉 conversation_filter
# 并不够 —— 上限会从文本里再次推导出来。
# 直接删掉价格短语才稳)。
_PRICE_PHRASE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|块|万|w|rmb|￥|¥)?\s*"
    r"(?:以内|以下|以上|之内|内|封顶|不超过|不要超过|左右|起步|起|上下)"
    r"|(?:不要?超过|最多|顶多)\s*[¥￥]?\s*\d+(?:\.\d+)?\s*(?:元|块)?"
    r"|预算\s*\d*(?:\s*(?:元|块|万))?"
    r"|\d+\s*(?:到|-|~|至)\s*\d+\s*(?:元|块)?"
    r"|便宜(?:点|些|一点|实惠)?|平价|实惠|性价比|划算|高端|高档"
    # R13 — 英文预算措辞:让预算筛空的兜底对 "headphones under 1000"
    # 也能像放宽 "1000以内的耳机" 一样放宽。
    r"|(?:under|below|within|up to|no more than|less than|cheaper than|max\.?|≤|<=?)\s*[¥￥$]?\s*\d+(?:\.\d+)?",
    re.IGNORECASE,
)


def _strip_price(text: str) -> str:
    """删掉价格/预算短语;保留品类词供兜底检索使用。"""
    if not text:
        return text
    return _PRICE_PHRASE_RE.sub("", text).strip(" ,，、的")


# R13 cluster-D — 剥掉排除从句("推荐咖啡,不要速溶的" → "推荐咖啡"),
# 让下面"排除筛空"的探测能回答"这个**品类**到底有没有货"。
# (?!超过) 保住预算短语 "不要超过300" —— 那是价格上限,
# 不是排除条件。
_NEGATION_CLAUSE_RE = re.compile(
    r"[，,、;；\s]*(?:但|可是)?(?:不想要|不需要|不要(?!超过)|别要|别给我|不考虑|不买|不选|不含|不带|排除|除了)[^，。,;；]*"
    r"|[，,、;；\s]*\b(?:no|without)\s+[a-z][a-z '\-]*",
    re.IGNORECASE,
)


def _strip_negation(text: str) -> str:
    """删掉排除从句;保留正向的购物需求。"""
    if not text:
        return text
    return _NEGATION_CLAUSE_RE.sub("", text).strip(" ,，、;；的")


# R11.demo-fix — 英文购物词 → 中文品类提示。目录是中文的;英文查询
# ("recommend a sunscreen")会路由到笨重的多语言 reranker,而它会排错序
# (曾把 防晒 压到手机下面,LLM 随即误称"没有防晒")。在查询后追加中文品类词
# 能恢复中文 dense+BM25 召回,并给 reranker 锚定方向。
# 只做增补(augment),绝不替换原查询。
# R13 — 映射表 + 增补器移到了 rag/retrieve/english_terms.py,
# 让硬过滤推断(constraints.py)共享同一份映射:英文查询现在能抽出
# 和中文等价查询相同的 category/sub_category WHERE 条件,
# 而不是只剩价格过滤、返回一堆不相关的卡。
_augment_english = augment_english_query


# 主路由:POST /chat/stream → SSE 流式响应。**整个后端最重要的函数**。
# 流程见文件顶部 docstring 的 7 步。本函数把这 7 步串起来:
#   503 ready 检查 → 提取文本/图片 → 检索(CLIP or hybrid+rerank) →
#   detect cart 意图 → 算 cache key → 构造 history → 嵌套 generator 流式吐 SSE。
# 返回 StreamingResponse 走 SSE。LLM 还没产生 token 时 HTTP 头已经发出去了,
# 这样 iOS 端能"立即"知道请求被接受。
# R12 — 英文模式的文案。助手按 UI 语言回复:system prompt 追加英文输出指令,
# 购物车固定话术 / 空查询提示也都有英文版本。中文的 prompt *指令*
# (对比/场景/超范围/预算等附加段)保持中文 —— 它们负责引导模型,
# 而有了这个附加段,模型仍会用英文作答。
_EN_REPLY_ADDENDUM = (
    "\n\n## LANGUAGE — HIGHEST PRIORITY\n"
    "Respond ONLY in English: every sentence, bullet, label and product "
    "explanation must be English. Translate Chinese product titles and brands "
    "naturally (you may keep the original in parentheses once). For provenance "
    "tags use [catalog✓] for facts taken from the catalog below and [inferred?] "
    "for anything inferred — never the Chinese tags [目录✓]/[推断?]."
)

_CART_REPLIES = {
    "zh": {
        "checkout": "好的,正在为你结算 ✅",
        "add": "已加入购物车 ✅",
        "remove": "已从购物车移除 ✅",
        "set_quantity": "已更新购物车数量 ✅",
        "_": "好的 ✅",
    },
    "en": {
        "checkout": "Sure — taking you to checkout ✅",
        "add": "Added to your cart ✅",
        "remove": "Removed from your cart ✅",
        "set_quantity": "Cart quantity updated ✅",
        "_": "Done ✅",
    },
}


@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    # /ready 门控:Tujie 加的 readiness 检查。bge / cross-encoder / BM25 没预热完之前
    # 返回 503,iOS 收到后可以重试,不会拿到一个超慢的首条请求。
    if not getattr(request.app.state, "retrieval_ready", False):
        raise HTTPException(status_code=503, detail="retrieval is warming up; retry after /ready returns ready")

    t_received = time.perf_counter()
    user_text = _extract_user_text(req.messages)
    # R12 — UI 语言决定助手的回复语言(默认 zh)。
    # R13 — 用户明显在打英文时,以**消息**语言为准:英文提问被中文回答,
    # 不管 UI 开关怎么设都很违和
    # (而且 API 探针基本不会传 `language`)。
    lang = "en" if ((req.language or "").lower().startswith("en") or looks_english(user_text)) else "zh"
    retrieval_query = _augment_english(build_retrieval_query(req.messages), user_text)
    # R11.fix — 不带图的空/纯空白输入:跳过检索**和** LLM。
    # 空 user 消息会让 provider 返回 400(还会把原始上游错误漏给客户端),
    # 而检索会返回随机的默认卡。
    # 由下面 generator 里的固定澄清话术作答。
    empty_query = (not user_text.strip()) and not _has_image(req.messages)

    # 检索 ----------------------------------------------------------------------
    # R8.E.3:top_k* 是同步函数(底层是 torch / sentence-transformers)。
    # 直接在 FastAPI 事件循环上跑,会把**所有**其他请求阻塞整段时间 ——
    # 这正是 /chat/stream 进行中时 /cache/stats 会超时的原因。
    # asyncio.to_thread 把它卸载到默认线程池,事件循环保持响应
    # (与下面早已线程化的
    # normalize_product_prices 调用做法一致)。
    products: list[dict] = []
    img_bytes_list: list[bytes] = []
    # R10 #5 — 主动反问:请求太模糊、没法推荐时,完全跳过检索
    # (不出商品卡),让 LLM 通过下面的 _CLARIFY_PROMPT
    # 反问澄清。
    clarify_dims = _needs_clarification(user_text, req.messages)
    # R11.demo-fix — 超出经营范围(汽车/机票/房产/天气/...):压掉随机商品卡,
    # 让 LLM 礼貌拒答(见下面的附加段)。
    out_of_domain = (
        clarify_dims is None and not empty_query
        and not _has_image(req.messages) and _is_out_of_domain(user_text)
    )
    # 当预算/约束把结果过滤到空、我们兜底取了最接近的商品时置位 ——
    # 驱动"这些可能超预算"的附加说明。
    budget_relaxed = False
    # R13 cluster-D — 排除条件的话术状态。应用用户排除条件后仍有结果时,
    # `exclusion_note` 为 ("active", names)(LLM 必须说"已排除X",
    # 绝不能说"目录没有X");排除条件把该品类筛到零时为
    # ("emptied", names, examples)(LLM 必须说"都被排除了",
    # 而不是"没有该品类")。`no_match` 标记真正空手而归的检索
    # (cluster C:压掉商品卡,并要求 LLM 如实说明)。
    exclusion_note: tuple | None = None
    no_match = False
    conversation_filter = None
    # R13 — 场景查询("露营要带的东西")本来就会检回低分、跨类目的商品;
    # 相关性门控不能把它们判成无匹配。在这里(检索之前)检测,
    # 下面的场景附加段还会复用这个结果。
    scene_kw = _detect_scene(user_text)
    # R10 #5 — 反问澄清轮的可点按快捷回复 chips。每个 chip 都是一条现成的下一句;
    # 点一下就发出具体信号(品类/预算/对象),
    # 让**下一轮**可以正常检索。
    clarify_chips: list[str] = []
    if clarify_dims:
        if _GIFT_HINT.search(user_text):
            clarify_chips = (
                ["For my girlfriend", "For my boyfriend", "For my parents",
                 "For a friend", "Under ¥300", "Under ¥800", "Birthday gift"]
                if lang == "en" else
                ["送女友", "送男友", "送长辈", "送朋友",
                 "预算 300 以内", "预算 300-800", "生日礼物"])
        else:
            clarify_chips = (
                ["Beauty & skincare", "Electronics", "Sportswear",
                 "Snacks", "Under ¥500", "Under ¥1000"]
                if lang == "en" else
                ["美妆护肤", "数码电子", "服饰运动", "食品零食",
                 "500 元以内", "1000 左右"])
    if clarify_dims is None and not empty_query and not out_of_domain:
        if _has_image(req.messages):
            img_bytes_list = _extract_image_bytes_list(req.messages)
            # CLIP 检索器是单图的;用第一张附件作为视觉查询。
            # LLM 仍能通过 content 数组看到全部图片。
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
                relevance_gate=not scene_kw,
            )
        products = await asyncio.to_thread(normalize_product_prices, products)
        # R11.demo-fix — 绝不把空目录交给 LLM。硬性价格/品牌约束可能把结果
        # 全部筛掉(比如最便宜的耳机是 ¥1686 时问 "500 元以内的耳机",
        # 或最便宜的笔记本是 ¥6299 时问 "5000 以内笔记本");
        # 空目录曾让 LLM 声称 "商品目录为空 / 请导入目录"。
        # 剥掉价格措辞后重新检索同一**品类**,保证总能展示最接近的商品;
        # budget_relaxed 告诉 LLM 这些候选可能超预算。
        if not products and not _has_image(req.messages) and user_text.strip():
            # 重新应用 "不要X的Y" → "Y 不要X" 的语序调整:带逗号的形式
            # ("不要苹果的耳机，预算500")在上游从未被调整过(正则带锚点,
            # 逗号会挡住匹配),所以这里剥掉价格后,裸的 "不要苹果的耳机"
            # 才第一次出现 —— 调整语序后,兜底检索只排除该品牌,
            # 而不是把整个品类丢掉。
            fb_query = _reorder_negation_object(_strip_price(retrieval_query) or retrieval_query)
            fb = await asyncio.to_thread(
                top_k, fb_query, k=5,
                intent_text=(_strip_price(user_text) or None),
                user_id=req.user_id,
                relevance_gate=not scene_kw,
            )
            products = await asyncio.to_thread(normalize_product_prices, fb)
            budget_relaxed = bool(products)

        # R13 cluster-D — 感知排除条件的话术(标志位说明见上)。
        if not _has_image(req.messages) and user_text.strip():
            excluded_names: list[str] = []
            if conversation_filter is not None:
                excluded_names += list(conversation_filter.brand_exclude or [])
                excluded_names += list(conversation_filter.exclude_keywords or [])
            try:
                from app.services.rag_client import _negation_signals
                has_negation = _negation_signals(user_text) or _negation_signals(retrieval_query)
            except Exception:
                has_negation = bool(excluded_names)
            if not products and (excluded_names or has_negation):
                # 排除条件可能把整个品类筛到了零("推荐咖啡,不要速溶的"
                # 而目录里的咖啡恰好全是速溶)。去掉排除从句后探测一次该品类:
                # 如果有货,诚实的回答是"都被排除了",绝不是"目录没有咖啡"。
                # 探测结果只作为 LLM 的上下文 ——
                # 它们违反了用户的排除条件,
                # 所以**不会**作为商品卡发出。
                relaxed_q = _strip_negation(_strip_price(retrieval_query) or retrieval_query)
                if relaxed_q and relaxed_q != retrieval_query:
                    try:
                        relaxed = await asyncio.to_thread(
                            top_k, relaxed_q, k=3,
                            intent_text=(_strip_negation(_strip_price(user_text)) or None),
                        )
                    except Exception:
                        relaxed = []
                    if relaxed:
                        examples = "、".join((p.get("title") or "")[:24] for p in relaxed[:3])
                        exclusion_note = ("emptied", excluded_names, examples)
            elif products and excluded_names:
                exclusion_note = ("active", excluded_names, "")
            no_match = not products and exclusion_note is None and not budget_relaxed
    t_retrieval = time.perf_counter()

    # 购物车意图检测 -------------------------------------------------------------
    # 反问澄清轮绝不会是购物车操作 —— 跳过检测,
    # 避免模糊的"帮我挑个东西"被误读。
    cart_event = None if clarify_dims else _detect_cart_intent(user_text)

    # 缓存 -----------------------------------------------------------------------
    # R8.E:多附件消息现在对所有图片 SHA **排序后**再拼接哈希,
    # 缓存 key 与图片顺序无关且长度有界。
    # R9.B-FIX:把用户**当前**的偏好状态折进 key。偏好先验(提案 #12)
    # 在 top_k 内部给商品重排序,但响应缓存命中时回放的是存储的事件 ——
    # 不加这一项的话,点完 👍/👎 再问**同一个**查询,
    # 回放的还是过期的(偏好生效前的)顺序。把权重哈希进 key 意味着
    # 用户偏好一变,该用户的缓存随即失效,
    # 会重新生成一份重排后的新响应。没点过 👍/👎 的用户该值为空,
    # 所以跨用户的缓存共享不受影响。
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

    # 组装 messages + provider ----------------------------------------------------
    # R9.A.5 — 按查询形态给 system prompt 追加附加段。
    #   * 对比意图:引导 LLM 用 markdown 表格输出并排对比的答案
    #     (提案 #10)。
    #   * 场景查询:要求 LLM 跨类目挑选互补商品,
    #     而不是同一商品的三个变体
    #     (提案 #9 场景搭配 scene builder)。
    addendum = ""
    if _is_comparison_query(user_text):
        addendum += (
            "\n\n6. **本轮是商品对比 —— 立刻输出对比表,严禁反问或确认。**\n"
            "用户没点名具体商品时(『对比一下/哪个好/哪个更好/这几款/这些/它们/有什么区别』),"
            "**默认就把下面『商品目录』里的全部商品拿来对比,无论几款**。\n"
            "**严禁**出现『你想对比哪几款』『是否要全部对比』『请确认』这类反问或确认句——"
            "用户已经说了要对比,直接给表格。\n"
            "格式:Markdown 表格,每个商品一列,行包括「价格」「主要特点」「适用场景」「优劣势」;"
            "表格里只能出现下面目录里的商品,不要加目录外的。示例:\n"
            "| 维度 | 商品A | 商品B |\n"
            "| --- | --- | --- |\n"
            "| 价格 | ¥720 | ¥760 |\n"
            "| 适用场景 | 熬夜修护 | 日常稳肌 |\n"
        )
    if scene_kw:
        addendum += (
            f"\n\n7. **本轮是场景搭配 ({scene_kw})**: 不要推同类目 3 个,而是从目录里挑 "
            "3-4 件互补的商品凑成一套(尽量来自不同类目, 比如食品+数码+服饰)。每件用 "
            "[目录✓] 标注价格/品牌,简短说明该件在该场景中的用途。"
        )
    # R11.demo-fix — 预算把结果筛空了;我们已兜底取最接近的商品。
    # 告诉 LLM 这些候选可能超预算,让它如实表述,
    # 而不是声称目录为空。
    if budget_relaxed:
        addendum += (
            "\n\n8. **预算无完全匹配**: 没有完全符合用户预算/约束的商品,下面的候选是目录里"
            "**最接近**的(可能超出预算或不完全匹配)。请如实说明这一点(例如『没有 X 元以内的,"
            "目录里最接近的是这几款』)并推荐其中最接近的,**绝不要**说目录为空。"
        )
    # R11.demo-fix — 超出经营范围:本轮不出卡;指示 LLM 礼貌拒答。
    if out_of_domain:
        addendum += (
            "\n\n8. **超出经营范围**: 用户问的是本商城不经营的商品/服务(如汽车/机票/酒店/房产/"
            "天气等)。请礼貌说明你是电商导购助手、目录里没有该类商品(这属于纪律第二条的例外,"
            "无需推荐替代商品),并引导用户说出想买的商品品类(美妆/数码/服饰/食品等),不要推荐"
            "任何不相关商品。"
        )
    # R13 cluster-D — 感知排除条件的措辞。绝不让 LLM 把**用户自己排除**的东西
    # 说成目录"没有"。
    if exclusion_note:
        _kind, _excluded, _examples = exclusion_note
        _names = "、".join(dict.fromkeys(_excluded))
        if _kind == "active":
            addendum += (
                f"\n\n9. **已应用用户的排除条件**: 已按用户要求排除「{_names}」。"
                f"被排除的品牌/商品在目录中**可能仍在售**,只是用户不要它们——"
                f"**绝不要**说『目录暂无{_names}』『没有{_names}』,"
                "要表述成『已为你排除…,以下是其他选择』。"
            )
        else:
            addendum += (
                "\n\n9. **排除条件清空了结果**: 目录里其实有该品类的商品(例如 "
                f"{_examples}),但**全部命中了用户的排除条件**"
                + (f"(如「{_names}」)" if _names else "")
                + "。请如实说明『该品类的商品都属于被排除的类型』,**不要**说目录没有该品类;"
                "可以问用户要不要放宽条件。本轮没有商品卡。(此为纪律第二条的例外,无需推荐替代商品)"
            )
    # R13 cluster-C — 真的没有相关商品:商品卡已被压掉(相关性门控),
    # 因此要求 LLM 如实说明,而不是编造。
    if no_match:
        addendum += (
            "\n\n10. **没有匹配的商品**: 检索没有找到与本次需求相关的商品,本轮没有商品卡。"
            "请用一两句话如实说明目录暂时没有该类商品,并引导用户换一个品类"
            "(美妆/数码/服饰/食品等)。不要编造商品,也不要硬塞无关推荐。(此为纪律第二条的例外)"
        )
    # R10 #5 — 反问澄清轮换用反问 prompt(不带目录,
    # 也不带对比/场景附加段),让 LLM 反问而不是推荐。
    if clarify_dims:
        system = _CLARIFY_PROMPT.format(dimensions=clarify_dims)
    else:
        system = _PROMPT.format(catalog=_build_catalog(products)) + addendum
    if lang == "en":
        system += _EN_REPLY_ADDENDUM
    # R13 — 带上之前的文本轮次,让 LLM 拥有检索层早已在用的对话上下文
    # (见 _prior_text_turns)。
    prior_turns = _prior_text_turns(req.messages)
    if _has_image(req.messages):
        last_user = next((m for m in reversed(req.messages) if m.role == "user"), None)
        user_content = last_user.model_dump()["content"] if last_user else user_text
        # Bug 2 修复(R8.F):调用视觉 LLM 前先把 iPhone 全尺寸照片缩到 1024px。
        # 典型的 12MP iPhone 上传图 token 开销约降 12×,
        # 正是这个开销曾把 3 图请求拖过 30s。
        user_content = _downscale_message_content(user_content, max_edge=1024)
        history = [
            {"role": "system", "content": system},
            *prior_turns,
            {"role": "user", "content": user_content},
        ]
    else:
        history = [
            {"role": "system", "content": system},
            *prior_turns,
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

        # 购物车意图最先发,iOS 才能立刻更新 UI。
        # R11.fix — 只实时发送,**不要**追加进 events_to_cache:缓存命中时,
        # 缓存里的 cart_intent 会叠在这条实时事件之上回放,
        # 客户端就会收到两次(重复加购/删除/结算)。这个事件由查询确定性推出、
        # 开销极低,所以每次都实时生成才是正确做法。
        if cart_event:
            yield _sse(cart_event)
            # R12-fix — 购物车操作类话语("帮我下单" / "加入购物车" /
            # "删掉第二个" / "改成2个")是**指令**,不是商品搜索。
            # 短路处理:跳过检索卡 + LLM,确保绝不会发出与动作矛盾的回复
            # (比如下单面板已经弹出,文字却说 "目录没货,无法下单")。
            # 只发一句固定话术。
            _replies = _CART_REPLIES.get(lang, _CART_REPLIES["zh"])
            _cart_reply = _replies.get(cart_event.get("action", ""), _replies["_"])
            yield _sse({"type": "delta", "text": _cart_reply})
            yield _sse({"type": "done"})
            return

        # R10 #5 — 澄清 chips 提前发出,iOS 才能把可点按的快捷回复
        # 和反问问题一起渲染。与 cart_event 同理:
        # 只实时发送、绝不缓存(否则命中时会重复发出)。
        if clarify_chips:
            yield _sse({"type": "clarify", "chips": clarify_chips})

        # R11.fix — 空/纯空白输入:固定澄清话术,不调 LLM
        # (空 content 会让 provider 返回 400)。一条澄清 + 一句友好引导。
        if empty_query:
            if lang == "en":
                yield _sse({"type": "clarify",
                            "chips": ["Moisturizer", "Running shoes",
                                      "Noise-cancelling headphones", "Snacks"]})
                yield _sse({"type": "delta",
                            "text": "What are you looking for? Just name a category, "
                                    "budget or occasion — e.g. “noise-cancelling "
                                    "headphones under ¥500”."})
            else:
                yield _sse({"type": "clarify",
                            "chips": ["推荐保湿面霜", "推荐运动鞋", "推荐降噪耳机", "推荐零食"]})
                yield _sse({"type": "delta",
                            "text": "你想找点什么呢?直接说品类、预算或场景就行,"
                                    "比如「五百元以内的降噪耳机」。"})
            yield _sse({"type": "done"})
            return

        # 缓存命中:快速回放。
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

        # R10 #4.4⭐⭐ — 首屏极速响应(调整 pipeline 顺序)。商品卡**先于**
        # LLM 文字流发出。`products` 已是完整重排后的生产候选集
        # (在进入本 generator 之前就算好了),所以这只是纯粹的**顺序调整**——
        # 质量零变化,LLM 依然基于完全相同的候选集回答。
        # 效果:检索一结束用户就能看到商品(缓存命中 ~0.3s / 热启动 1s 内),
        # 而不必等整个 LLM 生成结束、
        # 卡片才姗姗出现。
        for p in products:
            if await request.is_disconnected():
                return
            ev = _product_card_event(p)
            yield _sse(ev)
            events_to_cache.append(ev)

        # 缓存未命中:走 LLM 流式生成,带重试/退避。
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
            # 原始上游错误(模型名、URL、request id)绝不能漏给客户端 ——
            # 记入日志,对外只展示固定的友好提示。
            log.warning(f"LLM stream failed after retries: {e}")
            err = {"type": "error", "message": "抱歉，服务暂时不可用，请稍后重试。", "code": "UPSTREAM"}
            yield _sse(err)
            events_to_cache.append(err)

        # R9.A.5 — 提案 #8 事实核查:统计助手回复里的来源标签数量。
        # iOS 在消息气泡下方渲染一行小字
        # "✓ N 条已验证 · ? M 条推断",
        # 用户不用展开任何内容就能看到陈述级的透明度。
        full_text = "".join(assistant_text_chunks)
        marker_counts = _count_claim_markers(full_text)
        if marker_counts["verified"] or marker_counts["inferred"]:
            claim_ev = {"type": "claim_summary", **marker_counts}
            yield _sse(claim_ev)
            events_to_cache.append(claim_ev)

        done = {"type": "done"}
        yield _sse(done)
        events_to_cache.append(done)

        # 只缓存干净、完整的流:必须有真实文本(≥1 个 delta)**且**全程无错误。
        # 旧的判断只看 delta,于是吐了几个 token 然后才报错的流也被缓存了 ——
        # 之后整个 TTL 内,同一查询的每次重复都在回放那个错误(和过期文本),
        # 而不是重新去请求其实已经恢复的上游。
        has_delta = any(e.get("type") == "delta" for e in events_to_cache)
        has_error = any(e.get("type") == "error" for e in events_to_cache)
        if has_delta and not has_error:
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
    # uvicorn 默认不把 named logger 的 info 输出到 stdout;用 print
    # 让耗时日志无需额外配置就出现在 access log 旁边。
    print(json.dumps(record, ensure_ascii=False), flush=True)
