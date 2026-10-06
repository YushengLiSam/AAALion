"""智能体路径单测(假 LLM,检索打桩 —— 不加载模型、不联网、不花钱)。

覆盖:工具只收紧不放宽 / 服务端按 ID 回填并丢弃未检索到的 ID / 超时与出错回退 /
轮数上限 / 前导文本只进 trace / 规则路由。
"""

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest

from app.agent import tools as agent_tools
from app.agent.fake_llm import ScriptedToolLLM
from app.agent.graph import AgentLimits, backfill, run_agent
from app.agent.router import bundle_budget, should_use_agent
from app.agent.tools import (
    SearchProductsArgs,
    ToolContext,
    execute_tool,
    openai_tool_specs,
    resolve_session_constraints,
    tighten_filter,
)
from app.services import currency, rag_client
from app.services.currency import ExchangeRate
from app.services.llm_provider import EchoProvider
from rag.retrieve.query import Filter

_SEED = REPO_ROOT / "data" / "seed"


def _catalog(pid: str) -> dict:
    return json.loads(next(_SEED.glob(f"*/data/{pid}.json")).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fixed_fx(monkeypatch):
    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate",
                        lambda s, t: ExchangeRate(s, t, 7.0, "2026-10-01"))
    yield
    currency.clear_rate_cache()


class _TopKSpy:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, text, k=5, filters=None, **kw):
        self.calls.append({"text": text, "k": k, **kw})
        return [dict(p) for p in self.results]


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
#  只收紧、不放宽
# --------------------------------------------------------------------------- #

def test_llm_cannot_raise_session_budget():
    f, notes = tighten_filter(Filter(price_max_cny=1000), price_max_cny=5000)
    assert f.price_max_cny == 1000
    assert notes and "会话上限" in notes[0]


def test_llm_can_lower_budget_and_raise_floor():
    f, notes = tighten_filter(Filter(price_max_cny=1000, price_min_cny=100),
                              price_max_cny=800, price_min_cny=300)
    assert (f.price_max_cny, f.price_min_cny) == (800, 300)
    assert notes == []


def test_llm_cannot_lower_session_floor():
    f, _ = tighten_filter(Filter(price_min_cny=500), price_min_cny=10)
    assert f.price_min_cny == 500


def test_session_exclusions_survive_and_union():
    sess = Filter(brand_exclude=["耐克"], exclude_keywords=["日系"])
    f, _ = tighten_filter(sess, brand_exclude=["阿迪达斯"])
    assert "耐克" in f.brand_exclude and "阿迪达斯" in f.brand_exclude
    assert f.exclude_keywords == ["日系"]


def test_llm_cannot_include_excluded_brand():
    f, _ = tighten_filter(Filter(brand_exclude=["耐克"]), brand_include=["耐克"])
    # 被排除的品牌不能被 include 回来:剩下一个不可能满足的品牌 → 空结果
    assert all("耐克" not in b for b in f.brand_include)


def test_brand_include_intersects_with_session_brand():
    sess = Filter(brand_include=["华为"])
    f, notes = tighten_filter(sess, brand_include=["小米"])
    assert f.brand_include == ["华为"]          # 冲突 → 保留会话品牌,不放宽
    assert notes


def test_bundle_budget_caps_single_item():
    f, _ = tighten_filter(Filter(), bundle_budget_cny=3000, price_max_cny=8000)
    assert f.price_max_cny == 3000


def test_unknown_category_ignored_not_loosened():
    f, notes = tighten_filter(Filter(category="服饰运动"), category="数码电子")
    assert f.category == "服饰运动"
    assert notes


def test_resolve_session_keeps_only_hard_constraints():
    conv = Filter(category="数码电子", brand_include=["Apple 苹果"], price_max_cny=2000,
                  brand_exclude=["Bose"], exclude_keywords=["日系"])
    s = resolve_session_constraints(conv, {"price_max": 1500})
    assert s.price_max_cny == 1500                # 取更严的
    assert s.brand_exclude == ["Bose"] and s.exclude_keywords == ["日系"]
    assert s.brand_include is None and s.category is None   # 锚点品牌不当硬约束


def test_search_products_enforces_session_even_if_retrieval_leaks(monkeypatch):
    cheap = {**_catalog("p_clothes_020")}                  # ¥79
    pricey = {**_catalog("p_clothes_008")}                 # ¥1399
    spy = _TopKSpy([pricey, cheap])
    monkeypatch.setattr(rag_client, "top_k", spy)
    ctx = ToolContext(session=Filter(price_max_cny=1000))
    out = execute_tool(ctx, "search_products", {"query": "跑步", "price_max_cny": 99999})
    sent = spy.calls[0]["conversation_filter"]
    assert sent.price_max_cny == 1000                      # LLM 的 99999 被压回
    assert spy.calls[0]["skip_topic_switch"] is True
    assert [r["id"] for r in out["results"]] == ["p_clothes_020"]
    assert "p_clothes_008" not in ctx.retrieved            # 漏网的贵货进不了可引用集合
    assert out["notes"]


def test_search_products_rejects_invalid_args():
    ctx = ToolContext()
    out = execute_tool(ctx, "search_products", {"query": "", "k": 99})
    assert out["error"] == "invalid_arguments"
    assert execute_tool(ctx, "drop_table", {})["error"] == "unknown_tool"


def test_get_product_catalog_only_and_respects_session():
    ctx = ToolContext(session=Filter(price_max_cny=500))
    assert execute_tool(ctx, "get_product", {"product_id": "p_not_real"})["error"] == "unknown_product_id"
    out = execute_tool(ctx, "get_product", {"product_id": "p_digital_007"})   # ¥1699 > 500
    assert out["citable"] is False and "p_digital_007" not in ctx.retrieved
    out = execute_tool(ctx, "get_product", {"product_id": "p_clothes_020"})   # ¥79
    assert out["citable"] is True and "p_clothes_020" in ctx.retrieved


def test_price_of_converts_usd_with_same_rate():
    out = execute_tool(ToolContext(), "price_of", {"product_id": "p_2_intl_02"})
    assert out["price_cny"] == pytest.approx(249 * 7.0)
    assert out["source_currency"] == "USD" and out["source_price"] == 249.0
    assert out["fx_rate_date"] == "2026-10-01"


def test_compare_only_retrieved_ids():
    ctx = ToolContext()
    ctx.remember([currency.normalize_product_price(_catalog("p_2_intl_01")),
                  _catalog("p_digital_007")])
    out = execute_tool(ctx, "compare", {"product_ids": ["p_2_intl_01", "p_digital_007", "p_digital_018"]})
    assert [r["id"] for r in out["rows"]] == ["p_2_intl_01", "p_digital_007"]
    assert out["rejected_ids"] == ["p_digital_018"]
    assert out["cheapest"] == "p_digital_007"              # ¥1699 < ¥2786(按人民币)


def test_tool_specs_are_openai_function_schemas():
    specs = {s["function"]["name"]: s for s in openai_tool_specs()}
    assert set(specs) == {"search_products", "find_relative", "get_product", "price_of",
                          "compare", "submit_products"}
    params = specs["search_products"]["function"]["parameters"]
    assert params["required"] == ["query"]
    assert params["properties"]["price_max_cny"]["type"] == "number"
    assert "anyOf" not in json.dumps(params)


# --------------------------------------------------------------------------- #
#  回填 / 编排
# --------------------------------------------------------------------------- #

_RUN_SHOES = [_catalog("p_clothes_010"), _catalog("p_8_real_02")]


def test_backfill_drops_ids_not_retrieved_this_turn(monkeypatch):
    monkeypatch.setattr(rag_client, "top_k", _TopKSpy(_RUN_SHOES))
    llm = ScriptedToolLLM([
        [{"name": "search_products", "arguments": {"query": "跑步鞋"}}],
        [{"submit": ["p_8_real_02", "p_fake_999", "p_beauty_001", "p_clothes_010"]}],
    ])
    res = _run(run_agent("推荐跑鞋", provider=llm, ctx=ToolContext(), route_reason="x"))
    assert res.error is None
    assert res.product_ids == ["p_8_real_02", "p_clothes_010"]
    assert res.dropped_ids == ["p_fake_999", "p_beauty_001"]   # 目录外 / 本轮没检索到
    assert [p["product_id"] for p in res.products] == ["p_8_real_02", "p_clothes_010"]
    assert res.trace["dropped_ids"] == res.dropped_ids
    assert res.trace["llm_calls"] == 2
    assert res.trace["usage"]["total_tokens"] > 0


def test_backfill_helper_respects_session():
    ctx = ToolContext(session=Filter(price_max_cny=500))
    ctx.remember([_catalog("p_clothes_010")])              # 越过 search 直接塞进来的也拦
    products, kept, dropped = backfill(ctx, ["p_clothes_010"])
    assert products == [] and dropped == ["p_clothes_010"]


def test_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(rag_client, "top_k", _TopKSpy(_RUN_SHOES))
    llm = ScriptedToolLLM([[{"name": "search_products", "arguments": {"query": "跑鞋"}}]], delay_s=1.0)
    res = _run(run_agent("推荐跑鞋", provider=llm, ctx=ToolContext(), route_reason="x",
                         limits=AgentLimits(timeout_s=0.3)))
    assert res.error == "timeout"
    assert res.products == [] and not res.ok


def test_llm_error_and_unsupported_provider_fall_back(monkeypatch):
    monkeypatch.setattr(rag_client, "top_k", _TopKSpy(_RUN_SHOES))
    res = _run(run_agent("x", provider=ScriptedToolLLM([], fail_at=0), ctx=ToolContext()))
    assert res.error.startswith("llm_error") and res.products == []
    res = _run(run_agent("x", provider=EchoProvider(), ctx=ToolContext()))
    assert res.error == "unsupported_provider"


def test_max_three_tool_rounds_then_submit_only(monkeypatch):
    monkeypatch.setattr(rag_client, "top_k", _TopKSpy(_RUN_SHOES))
    search = [{"name": "search_products", "arguments": {"query": "跑鞋"}}]
    llm = ScriptedToolLLM([search, search, search, search, search])
    res = _run(run_agent("推荐跑鞋", provider=llm, ctx=ToolContext(), route_reason="x"))
    assert res.trace["rounds"] == 3
    assert len(llm.calls) == 4
    assert llm.calls[-1]["tools"] == ["submit_products"]   # 到顶后只给提交工具
    assert res.error is None and res.product_ids


def test_preamble_goes_to_trace_only(monkeypatch):
    monkeypatch.setattr(rag_client, "top_k", _TopKSpy(_RUN_SHOES))
    llm = ScriptedToolLLM([[{"name": "search_products", "arguments": {"query": "跑鞋"}}]],
                          content="好的,我来帮你查一下~")
    res = _run(run_agent("推荐跑鞋", provider=llm, ctx=ToolContext()))
    assert "好的,我来帮你查一下~" in res.trace["preambles"][0]
    assert all("好的" not in json.dumps(p, ensure_ascii=False) for p in res.products)


def test_find_relative_tool_uses_fixed_multihop(monkeypatch):
    anchor = _catalog("p_2_intl_02")
    cheap = {"product_id": "c1", "title": "测试耳机", "brand": "测试", "category": "数码电子",
             "sub_category": "真无线降噪耳机", "base_price": 999, "provenance": {"currency": "CNY"}}
    calls = []

    def fake_top_k(text, k=5, filters=None, **kw):
        calls.append(kw)
        return [dict(anchor)] if len(calls) == 1 else [dict(anchor), dict(cheap)]

    monkeypatch.setattr(rag_client, "top_k", fake_top_k)
    llm = ScriptedToolLLM([
        [{"name": "find_relative", "arguments": {"anchor": "AirPods Pro", "relation": "cheaper",
                                                 "target": "降噪耳机"}}],
        [{"submit": "auto"}],
    ])
    res = _run(run_agent("有没有比AirPods Pro便宜的降噪耳机", provider=llm, ctx=ToolContext()))
    assert res.product_ids == ["p_2_intl_02", "c1"]          # 参照商品在前
    assert res.trace["anchor_ids"] == ["p_2_intl_02"]
    hop = res.trace["hop_traces"][0]
    assert hop["anchor"]["price_cny"] == pytest.approx(249 * 7.0)   # Bug 1 修复贯通到工具层
    assert calls[1]["skip_topic_switch"] is True                    # Bug 2 修复贯通到工具层


# --------------------------------------------------------------------------- #
#  路由
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,reason", [
    ("有没有比AirPods Pro便宜的降噪耳机", "multihop"),
    ("跟特步跑鞋同价位的其他跑鞋", "multihop"),
    ("买了小米手机,配个耳机", "multihop"),
    ("3000元配一套跑步装备", "bundle"),
    ("预算2000配齐露营装备", "bundle"),
    ("iPhone和小米哪个好", "comparison"),
    ("索尼和Bose的降噪耳机哪个好", "comparison"),
    ("HOKA和特步跑鞋对比一下", "comparison"),
    ("美版AirPods Pro 2和国行AirPods Pro 3哪个划算", "cross_currency"),
])
def test_router_sends_complex_to_agent(text, reason):
    d = should_use_agent(text)
    assert d.use_agent and d.reason == reason


@pytest.mark.parametrize("text", [
    "推荐降噪耳机", "这两款哪个好", "推荐iphone", "500元以内的耳机", "露营要带的东西",
    "推荐一套护肤品", "华为手机怎么样", "推荐跑鞋，不要耐克", "买2套T恤", "对比一下这几款", "",
])
def test_router_keeps_simple_on_fast_path(text):
    assert not should_use_agent(text).use_agent


def test_bundle_budget_parsing():
    assert bundle_budget("3000元配一套跑步装备") == 3000
    assert bundle_budget("给我搭配一套5000元以内的通勤穿搭") == 5000
    assert bundle_budget("1万配齐露营装备") == 10000
    assert bundle_budget("推荐一套护肤品") is None
