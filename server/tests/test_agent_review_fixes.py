"""P2 复审补丁的回归测试(假 LLM / 不联网 / 不加载模型)。

  1. 预算配套路由:年龄 / 容量 / 型号数字不能被当成总预算(预算在工具层是硬上限);
  2. 智能体会话约束与快路同口径做话题切换检测:上一话题的预算 / 排除不能带进新话题;
  3. price_in_cny(fetch=False):已归一化的商品不再重复请求汇率源;
  4. agent_eval:不带 --fake-llm / --live 不会默默调用付费 LLM;所有用例共用一个事件循环;
  5. trace 文件超过体积上限时轮转。
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

from app.agent import runtime
from app.agent.router import bundle_budget, should_use_agent
from app.services import currency, rag_client
from app.services.constraint_state import build_conversation_filter
from app.services.currency import ExchangeRate, price_in_cny
from app.schemas.chat import ChatMessage
from rag.retrieve.query import Filter


# --------------------------------------------------------------------------- #
#  1. 预算配套路由
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [
    "适合30岁女生的一套护肤品",
    "给18岁男生搭配一套衣服",
    "推荐50ml一套的护肤套装",
    "iPhone 15 一套多少钱",
])
def test_bundle_ignores_numbers_without_money_marker(text):
    assert bundle_budget(text) is None
    route = should_use_agent(text)
    assert route.reason != "bundle"
    assert route.bundle_budget_cny is None


@pytest.mark.parametrize("text,budget", [
    ("3000元配一套跑步装备", 3000.0),
    ("预算2000配齐露营装备", 2000.0),
    ("5000以内搭配一套通勤穿搭", 5000.0),
    ("3k配一套跑步装备", 3000.0),
    ("¥3000一套露营装备", 3000.0),
    ("一套护肤品 预算800元", 800.0),
])
def test_bundle_still_detects_real_budgets(text, budget):
    assert bundle_budget(text) == budget
    assert should_use_agent(text).reason == "bundle"


def test_bundle_skips_age_then_finds_budget_in_same_sentence():
    # 第一个数字是年龄(无钱标记)→ 跳过;后面带"元"的才是预算
    assert bundle_budget("给30岁的我 800元配一套护肤品") == 800.0


# --------------------------------------------------------------------------- #
#  2. 会话约束的话题切换(与快路 top_k 同口径)
# --------------------------------------------------------------------------- #

def _msgs(*pairs):
    return [ChatMessage(role=r, content=c) for r, c in pairs]


def test_stale_budget_dropped_on_topic_switch():
    msgs = _msgs(("user", "500元以内的耳机"), ("assistant", "推荐……"),
                 ("user", "iPhone 和 小米 手机哪个好"))
    conv = build_conversation_filter(msgs, None)
    assert conv.effective_price_max_cny == 500.0          # 前提:继承下来了旧预算
    ctx = runtime._build_ctx(user_text="iPhone 和 小米 手机哪个好", conversation_filter=conv,
                             explicit_filters=None, user_id=None, bundle_budget_cny=None)
    assert ctx.session.price_max_cny is None              # 换话题:旧预算不再是硬约束


def test_stale_budget_does_not_cap_new_bundle():
    msgs = _msgs(("user", "300元以内的洗面奶"), ("assistant", "推荐……"),
                 ("user", "5000元配一套露营装备"))
    conv = build_conversation_filter(msgs, None)
    ctx = runtime._build_ctx(user_text="5000元配一套露营装备", conversation_filter=conv,
                             explicit_filters=None, user_id=None, bundle_budget_cny=5000.0)
    assert ctx.session.price_max_cny != 300.0
    # 本轮原话里的约束保留(与 top_k 换话题后 build_retrieval_filter(原话) 同口径)
    assert ctx.session.price_max_cny in (None, 5000.0)


def test_session_kept_when_no_topic_switch(monkeypatch):
    conv = Filter(price_max_cny=800.0, brand_exclude=["索尼"])
    monkeypatch.setattr(rag_client, "detect_topic_switch", lambda f, t: False)
    out = runtime.session_filter_for_turn("再便宜点的呢", conv)
    assert out is conv


def test_explicit_ios_filters_survive_topic_switch(monkeypatch):
    monkeypatch.setattr(rag_client, "detect_topic_switch", lambda f, t: True)
    ctx = runtime._build_ctx(user_text="iPhone 和 小米 手机哪个好",
                             conversation_filter=Filter(price_max_cny=500.0),
                             explicit_filters={"price_max": 6000, "exclude_brands": ["OPPO"]},
                             user_id=None, bundle_budget_cny=None)
    assert ctx.session.price_max_cny == 6000.0
    assert "OPPO" in (ctx.session.brand_exclude or [])


def test_topic_switch_detector_failure_keeps_session(monkeypatch):
    def boom(f, t):
        raise RuntimeError("x")
    monkeypatch.setattr(rag_client, "detect_topic_switch", boom)
    conv = Filter(price_max_cny=500.0)
    assert runtime.session_filter_for_turn("随便", conv) is conv


def test_top_k_uses_same_detector(monkeypatch):
    """top_k 与智能体共用 detect_topic_switch;skip_topic_switch=True 时根本不调用它。"""
    seen = []
    monkeypatch.setattr(rag_client, "detect_topic_switch", lambda f, t: seen.append(t) or False)
    monkeypatch.setattr(rag_client, "_heavy_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(rag_client, "_retrieval_cache_get", lambda key: None)
    monkeypatch.setattr(rag_client, "_retrieval_cache_put", lambda *a, **k: None, raising=False)
    rag_client.top_k("耳机", conversation_filter=Filter(price_max_cny=1.0), intent_text="耳机")
    assert seen == ["耳机"]
    rag_client.top_k("耳机", conversation_filter=Filter(price_max_cny=1.0), intent_text="耳机",
                     skip_topic_switch=True)
    assert seen == ["耳机"]


# --------------------------------------------------------------------------- #
#  3. price_in_cny(fetch=False)
# --------------------------------------------------------------------------- #

def test_price_in_cny_no_refetch_for_normalized(monkeypatch):
    calls = []

    def down(s, t):
        calls.append((s, t))
        raise RuntimeError("fx down")

    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate", down)
    usd = {"product_id": "x", "base_price": 249, "provenance": {"currency": "USD"}}
    assert price_in_cny(usd, fetch=False) is None
    assert calls == []
    cny = {"product_id": "y", "base_price": 199, "provenance": {"currency": "CNY"}}
    assert price_in_cny(cny, fetch=False) == 199.0
    assert price_in_cny({"base_price": 99}, fetch=False) == 99.0     # 缺 provenance 视为 CNY
    assert price_in_cny(usd) is None                                  # 默认仍会尝试一次
    assert len(calls) == 1
    currency.clear_rate_cache()


def test_assert_relation_does_not_refetch(monkeypatch):
    calls = []
    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate",
                        lambda s, t: calls.append(1) or (_ for _ in ()).throw(RuntimeError()))
    prods = [{"product_id": "a", "base_price": 100, "provenance": {"currency": "USD"}},
             {"product_id": "b", "base_price": 100, "provenance": {"currency": "CNY"}}]
    out = rag_client._assert_relation(prods, {"price_cny": 500}, "cheaper", Filter(price_max_cny=450))
    assert [p["product_id"] for p in out] == ["b"]   # 外币缺人民币价:剔除,不拿 $100 当 ¥100
    assert calls == []
    currency.clear_rate_cache()


# --------------------------------------------------------------------------- #
#  4. agent_eval 防手滑 + 事件循环复用
# --------------------------------------------------------------------------- #

def test_agent_eval_requires_explicit_live(monkeypatch):
    from rag.eval import agent_eval as ev
    import app.services.llm_provider as lp

    monkeypatch.setattr(lp, "get_provider", lambda: pytest.fail("must not build a paid provider"))
    monkeypatch.setattr(ev, "evaluate", lambda *a, **k: pytest.fail("must not evaluate"))
    assert ev.main(["--mode", "both"]) == 2
    assert ev.main(["--mode", "agent", "--fake-llm", "--live"]) == 2


def test_agent_eval_reuses_one_event_loop():
    from rag.eval import agent_eval as ev

    async def loop_id():
        return id(asyncio.get_running_loop())

    assert ev._run_in_eval_loop(loop_id()) == ev._run_in_eval_loop(loop_id())


# --------------------------------------------------------------------------- #
#  5. trace 轮转
# --------------------------------------------------------------------------- #

def test_trace_rotates_when_over_cap(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("AGENT_SHADOW_LOG", str(path))
    monkeypatch.setenv("AGENT_TRACE_MAX_MB", "0.0001")      # ≈105 字节
    runtime.append_trace({"pad": "x" * 200})
    runtime.append_trace({"n": 2})
    assert (tmp_path / "shadow.jsonl.1").exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l) for l in lines] == [{"n": 2}]


# --------------------------------------------------------------------------- #
#  6. 会话锚点多跳留在快路
# --------------------------------------------------------------------------- #

def test_history_anchor_multihop_stays_on_fast_path():
    from rag.retrieve.multihop import detect_multihop

    q = "有没有比刚才第二款便宜的"
    plan = detect_multihop(q, has_history_cards=True)
    assert plan is not None and plan.uses_history_anchor       # 前提:确实是会话锚点多跳
    route = should_use_agent(q, has_history_cards=True)
    assert route.use_agent is False and route.reason == "multihop_history"


def test_named_anchor_multihop_still_routes_to_agent():
    assert should_use_agent("比 AirPods Pro 便宜的降噪耳机").reason == "multihop"


def test_try_agent_products_threads_user_text_into_session(monkeypatch, tmp_path):
    """接线测试:runtime 真的把本轮原话传给了话题切换检测(不只是 _build_ctx 单测)。"""
    import app.agent.graph as graph
    from app.agent.graph import AgentResult
    from app.agent.router import RouteDecision

    monkeypatch.setenv("AGENT_SHADOW_LOG", str(tmp_path / "t.jsonl"))
    seen = {}

    async def fake_run_agent(user_text, *, provider, ctx, prior_turns=None, route_reason="", limits=None):
        seen["session"] = ctx.session
        return AgentResult(error="no_citable_products", trace={})

    monkeypatch.setattr(graph, "run_agent", fake_run_agent)
    q = "iPhone 和 小米 手机哪个好"
    conv = build_conversation_filter(_msgs(("user", "500元以内的耳机"), ("assistant", "…"), ("user", q)), None)
    products, _trace = asyncio.run(runtime.try_agent_products(
        q, route=RouteDecision(True, "comparison"), conversation_filter=conv,
        explicit_filters=None, user_id=None, prior_turns=[], provider=object()))
    assert products == []
    assert seen["session"].price_max_cny is None
