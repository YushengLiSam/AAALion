"""/chat/stream 接入智能体路径的集成测试(TestClient + 打桩检索 + Echo LLM)。

  * AGENT_PATH=off(默认):智能体模块一个函数都不调,SSE 事件序列与之前一致;
  * on:路由命中时商品卡来自智能体;智能体失败 / 超时回退快路;缓存 key 带 path 标签,
    智能体响应不会被当成快路响应回放(反之亦然);不出现新的 SSE 事件类型;
  * shadow:后台任务绝不影响响应,只往 JSONL 追加 trace。
"""

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import runtime as agent_runtime
from app.agent.fake_llm import ScriptedToolLLM
from app.routes import chat as chat_route
from app.services import currency, rag_client
from app.services.cache import cache
from app.services.currency import ExchangeRate
from app.services.llm_provider import EchoProvider

_SEED = REPO_ROOT / "data" / "seed"
_KNOWN_EVENTS = {"product_card", "delta", "done", "claim_summary", "hop_trace", "clarify",
                 "cart_intent", "error"}


def _catalog(pid: str) -> dict:
    return json.loads(next(_SEED.glob(f"*/data/{pid}.json")).read_text(encoding="utf-8"))


FAST = [_catalog("p_clothes_007"), _catalog("p_clothes_009")]
AGENT = [_catalog("p_clothes_010"), _catalog("p_clothes_020")]


@pytest.fixture
def client(monkeypatch, tmp_path):
    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate", lambda s, t: ExchangeRate(s, t, 7.0, "2026-10-01"))
    cache._d.clear()
    fast_calls = []

    def fake_top_k(text, k=5, filters=None, **kw):
        fast_calls.append(text)
        return [dict(p) for p in FAST]

    monkeypatch.setattr(chat_route, "top_k", fake_top_k)
    monkeypatch.setattr(rag_client, "top_k", fake_top_k)
    # 多跳路径打桩成"没锚到",让快路稳定走单跳
    monkeypatch.setattr(rag_client, "multi_hop_retrieve", lambda plan, **kw: (None, [], {}))
    monkeypatch.setattr(chat_route, "get_provider", lambda: EchoProvider())
    monkeypatch.setenv("AGENT_SHADOW_LOG", str(tmp_path / "shadow.jsonl"))
    monkeypatch.delenv("AGENT_PATH", raising=False)
    app = FastAPI()
    app.include_router(chat_route.router)
    app.state.retrieval_ready = True
    # 用 with:事件循环在多个请求之间保持存活(与 uvicorn 一致),
    # 影子模式的后台任务才有机会跑完。
    with TestClient(app) as c:
        c.fast_calls = fast_calls
        c.tmp_path = tmp_path
        yield c
    cache._d.clear()
    currency.clear_rate_cache()


def _events(client, text):
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": text}]})
    assert r.status_code == 200
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def _card_ids(events):
    return [e["product"]["product_id"] for e in events if e["type"] == "product_card"]


_AGENT_Q = "3000元配一套跑步装备"


def test_off_never_touches_agent(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("agent must not run when AGENT_PATH=off")

    monkeypatch.setattr(agent_runtime, "try_agent_products", boom)
    monkeypatch.setattr(agent_runtime, "schedule_shadow", boom)
    import app.agent.router as router_mod
    monkeypatch.setattr(router_mod, "should_use_agent", boom)
    ev = _events(client, _AGENT_Q)
    assert _card_ids(ev) == ["p_clothes_007", "p_clothes_009"]
    assert ev[-1]["type"] == "done"
    assert {e["type"] for e in ev} <= _KNOWN_EVENTS


def test_on_uses_agent_products_and_separate_cache(client, monkeypatch):
    calls = []

    async def fake_agent(user_text, **kw):
        calls.append(kw["route"].reason)
        return [dict(p) for p in AGENT], {"product_ids": [p["product_id"] for p in AGENT],
                                          "anchor_ids": []}

    monkeypatch.setattr(agent_runtime, "try_agent_products", fake_agent)

    ev_fast = _events(client, _AGENT_Q)                     # off:快路,写进缓存
    assert _card_ids(ev_fast) == ["p_clothes_007", "p_clothes_009"]

    monkeypatch.setenv("AGENT_PATH", "on")
    ev_agent = _events(client, _AGENT_Q)                    # 同一请求,on:不能回放快路缓存
    assert calls == ["bundle"]
    assert _card_ids(ev_agent) == ["p_clothes_010", "p_clothes_020"]
    # 卡片先于文字、协议不变、没有新事件类型
    types = [e["type"] for e in ev_agent]
    assert types.index("product_card") < types.index("delta")
    assert {e["type"] for e in ev_agent} <= _KNOWN_EVENTS
    assert len(cache) == 2                                  # 两条独立缓存

    monkeypatch.setenv("AGENT_PATH", "off")
    assert _card_ids(_events(client, _AGENT_Q)) == ["p_clothes_007", "p_clothes_009"]


def test_on_falls_back_to_fast_path_on_agent_failure(client, monkeypatch):
    async def failing(user_text, **kw):
        return [], {"error": "timeout"}

    monkeypatch.setattr(agent_runtime, "try_agent_products", failing)
    monkeypatch.setenv("AGENT_PATH", "on")
    ev = _events(client, _AGENT_Q)
    assert _card_ids(ev) == ["p_clothes_007", "p_clothes_009"]
    assert ev[-1]["type"] == "done"


def test_on_agent_exception_falls_back(client, monkeypatch):
    async def raising(user_text, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_runtime, "try_agent_products", raising)
    monkeypatch.setenv("AGENT_PATH", "on")
    assert _card_ids(_events(client, _AGENT_Q)) == ["p_clothes_007", "p_clothes_009"]


def test_on_simple_query_stays_on_fast_path(client, monkeypatch):
    async def fake_agent(user_text, **kw):
        raise AssertionError("simple query must not be routed to the agent")

    monkeypatch.setattr(agent_runtime, "try_agent_products", fake_agent)
    monkeypatch.setenv("AGENT_PATH", "on")
    assert _card_ids(_events(client, "推荐跑鞋")) == ["p_clothes_007", "p_clothes_009"]


def test_on_end_to_end_with_scripted_llm_and_real_runtime(client, monkeypatch):
    """不打桩 runtime:真实 LangGraph + 工具 + 回填,只有 LLM 是脚本、检索是桩。"""
    llm = ScriptedToolLLM([
        [{"name": "search_products", "arguments": {"query": "跑步鞋", "k": 2}}],
        [{"submit": ["p_clothes_009", "p_not_retrieved"]}],
    ])
    monkeypatch.setattr(chat_route, "get_provider", lambda: llm)
    monkeypatch.setenv("AGENT_PATH", "on")
    ev = _events(client, _AGENT_Q)
    assert _card_ids(ev) == ["p_clothes_009"]               # 未检索到的 ID 被丢弃
    rec = [json.loads(l) for l in (client.tmp_path / "shadow.jsonl").read_text().splitlines()]
    assert rec[-1]["mode"] == "on" and rec[-1]["dropped_ids"] == ["p_not_retrieved"]
    assert rec[-1]["fallback"] is False


def test_shadow_never_affects_response(client, monkeypatch):
    llm = ScriptedToolLLM([[{"name": "search_products", "arguments": {"query": "跑步鞋"}}]])
    monkeypatch.setattr(chat_route, "get_provider", lambda: llm)
    baseline = _events(client, _AGENT_Q)
    cache._d.clear()

    monkeypatch.setenv("AGENT_PATH", "shadow")
    shadow = _events(client, _AGENT_Q)
    assert _card_ids(shadow) == _card_ids(baseline)
    assert [e["type"] for e in shadow] == [e["type"] for e in baseline]

    log_path = client.tmp_path / "shadow.jsonl"
    for _ in range(50):                                     # 后台任务异步落盘
        if log_path.exists() and log_path.read_text().strip():
            break
        time.sleep(0.05)
    rec = json.loads(log_path.read_text().splitlines()[-1])
    assert rec["mode"] == "shadow" and rec["route"] == "bundle"
    assert rec["fast_product_ids"] == ["p_clothes_007", "p_clothes_009"]
    assert rec["llm_calls"] >= 1 and "usage" in rec and "latency_ms" in rec


def test_shadow_failure_is_invisible(client, monkeypatch):
    monkeypatch.setattr(chat_route, "get_provider",
                        lambda: ScriptedToolLLM([], fail_at=0))
    monkeypatch.setenv("AGENT_PATH", "shadow")
    ev = _events(client, _AGENT_Q)
    assert _card_ids(ev) == ["p_clothes_007", "p_clothes_009"] and ev[-1]["type"] == "done"


def test_busy_semaphore_goes_fast(client, monkeypatch):
    import asyncio

    monkeypatch.setenv("AGENT_MAX_CONCURRENCY", "1")
    sem = agent_runtime._semaphore()

    async def hold_and_try():
        async with sem:
            return await agent_runtime.try_agent_products(
                _AGENT_Q, route=__import__("app.agent.router", fromlist=["RouteDecision"]).RouteDecision(True, "bundle", 3000),
                conversation_filter=None, explicit_filters=None, user_id=None,
                prior_turns=[], provider=ScriptedToolLLM([]))

    products, trace = asyncio.run(hold_and_try())
    assert products == [] and trace["error"] == "busy"
