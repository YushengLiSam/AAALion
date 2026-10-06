"""OpenAI 兼容 provider 的非流式工具调用(chat_tools)。

用真实的 openai SDK(本地 venv 是 2.x,生产锁 1.51)+ httpx.MockTransport,
不发任何网络请求:
  * 归一化结构:前导 content + tool_calls(toolu_ id)+ 只保留标准 usage 字段;
  * 只对 chat_tools 关闭 SDK 重试:上游 500 只请求一次就抛;
  * 显式 timeout 真的传到了 httpx 请求上;
  * Echo / Anthropic provider 抛 NotImplementedError(智能体路径随之禁用)。
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

import httpx
import pytest
from openai import AsyncOpenAI

from app.services import llm_provider
from app.services.llm_provider import (
    EchoProvider,
    OpenAICompatibleProvider,
    _normalize_tool_response,
)

_TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_products",
        "description": "检索商品",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]},
    },
}]

_OK_BODY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "claude-haiku-4-5",
    "choices": [{
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": "我先查一下降噪耳机。",
            "tool_calls": [{
                "id": "toolu_01ABC",
                "type": "function",
                "function": {"name": "search_products",
                             "arguments": "{\"query\": \"降噪耳机\", \"price_max_cny\": 1500}"},
            }],
        },
    }],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
              "cache_read_input_tokens": 99, "vendor_secret": "x"},
}


def _provider(handler):
    p = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    p.name = "tokenrouter"
    p._model = "claude-haiku-4-5"
    p._client = AsyncOpenAI(
        api_key="test-key",
        base_url="http://fake.local/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return p


def test_chat_tools_normalizes_response_and_sends_tools():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_OK_BODY)

    out = asyncio.run(_provider(handler).chat_tools(
        [{"role": "user", "content": "比 AirPods 便宜的耳机"}], _TOOLS, timeout=3.0))

    assert out["content"] == "我先查一下降噪耳机。"
    assert out["tool_calls"] == [{"id": "toolu_01ABC", "name": "search_products",
                                  "arguments": {"query": "降噪耳机", "price_max_cny": 1500}}]
    assert out["usage"] == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    assert out["finish_reason"] == "tool_calls"
    assert out["model"] == "claude-haiku-4-5"

    body = json.loads(seen[0].content)
    assert body["stream"] is False
    assert body["tools"][0]["function"]["name"] == "search_products"
    assert body["tool_choice"] == "auto"
    # 显式超时传到了 httpx 请求上
    assert seen[0].extensions["timeout"]["read"] == pytest.approx(3.0)


def test_chat_tools_has_no_sdk_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(Exception):
        asyncio.run(_provider(handler).chat_tools([{"role": "user", "content": "x"}], _TOOLS))
    assert len(calls) == 1          # SDK 默认会重试 2 次;这里必须是 1


def test_chat_tools_accepts_tool_role_results():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        body = json.loads(json.dumps(_OK_BODY))
        body["choices"][0]["message"] = {"role": "assistant", "content": "推荐华为 FreeBuds。"}
        body["choices"][0]["finish_reason"] = "stop"
        return httpx.Response(200, json=body)

    msgs = [
        {"role": "user", "content": "比 AirPods 便宜的耳机"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "toolu_01ABC", "type": "function",
            "function": {"name": "search_products", "arguments": "{\"query\": \"耳机\"}"}}]},
        {"role": "tool", "tool_call_id": "toolu_01ABC", "content": "[{\"id\": \"p1\"}]"},
    ]
    out = asyncio.run(_provider(handler).chat_tools(msgs, _TOOLS))
    assert out["tool_calls"] == []
    assert out["finish_reason"] == "stop"
    assert seen[0]["messages"][2]["role"] == "tool"


def test_normalize_handles_malformed_arguments_and_missing_usage():
    out = _normalize_tool_response({
        "model": "m",
        "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "t1", "function": {"name": "get_product", "arguments": "{not json"}},
            {"id": "t2", "function": {"name": "compare", "arguments": ""}},
        ]}}],
    })
    assert out["tool_calls"][0] == {"id": "t1", "name": "get_product", "arguments": {},
                                    "raw_arguments": "{not json"}
    assert out["tool_calls"][1]["arguments"] == {}
    assert out["usage"] == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}


def test_stream_chat_untouched_and_echo_has_no_tools():
    assert hasattr(OpenAICompatibleProvider, "stream_chat")
    assert OpenAICompatibleProvider.supports_tools is True
    assert EchoProvider.supports_tools is False
    with pytest.raises(NotImplementedError):
        asyncio.run(EchoProvider().chat_tools([], _TOOLS))
    assert llm_provider.AnthropicProvider.supports_tools is False
