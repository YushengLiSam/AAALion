"""可插拔的 LLM provider 抽象层。

从以下几种实现中选其一:
  - ``anthropic`` — Claude API(豆包 key 补办期间的默认选项)。
  - ``doubao``    — ARK / 火山引擎(Volcengine),兼容 OpenAI 接口。
  - ``openai``    — OpenAI API。
  - ``echo``      — 无网络依赖的确定性 provider,供测试使用。

通过环境变量 ``LLM_PROVIDER`` 选择(默认:设置了 ANTHROPIC_API_KEY
则用 anthropic,否则回落到 echo)。
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Protocol


class LLMProvider(Protocol):
    name: str

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:  # noqa: D401
        """以流式方式逐块产出(yield)回复文本。"""
        ...


# 智能体路径(server/app/agent)用的**非流式工具调用**接口。只有 OpenAI 兼容
# provider 实现;其余 provider 抛 NotImplementedError,智能体路径随之禁用、
# 走原快路。返回结构做了归一化,与具体 SDK 版本(openai 1.51 / 2.x)无关:
#   {"content": str|None, "tool_calls": [{"id","name","arguments": dict}],
#    "usage": {"prompt_tokens","completion_tokens","total_tokens"},
#    "finish_reason": str|None, "model": str|None}
_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _normalize_tool_response(resp) -> dict:
    """把 chat.completions 的响应对象(或同形 dict)压成上面的标准结构。

    - 前导文本(模型在 tool_calls 旁边顺手说的话)原样放 content,由调用方决定
      只记 trace、不推前端;
    - arguments 是 JSON 字符串,解析失败时给空 dict 并保留原文到 raw_arguments,
      绝不抛异常(工具层会按 schema 再校验一遍);
    - usage 只保留三个标准字段——不同网关会塞各种私有字段,不往外漏。
    """
    def _get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    import json as _json

    choices = _get(resp, "choices") or []
    choice = choices[0] if choices else None
    message = _get(choice, "message")
    tool_calls: list[dict] = []
    for tc in _get(message, "tool_calls") or []:
        fn = _get(tc, "function")
        raw_args = _get(fn, "arguments")
        call = {"id": _get(tc, "id"), "name": _get(fn, "name"), "arguments": {}}
        if isinstance(raw_args, dict):
            call["arguments"] = raw_args
        elif isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = _json.loads(raw_args)
                if isinstance(parsed, dict):
                    call["arguments"] = parsed
                else:
                    call["raw_arguments"] = raw_args
            except ValueError:
                call["raw_arguments"] = raw_args
        tool_calls.append(call)
    usage_obj = _get(resp, "usage")
    usage = {}
    for f in _USAGE_FIELDS:
        v = _get(usage_obj, f)
        usage[f] = int(v) if isinstance(v, (int, float)) else None
    return {
        "content": _get(message, "content"),
        "tool_calls": tool_calls,
        "usage": usage,
        "finish_reason": _get(choice, "finish_reason"),
        "model": _get(resp, "model"),
    }


# --------------------------------------------------------------------------- #
#  Anthropic Claude(豆包 key 不可用期间的默认 provider)
# --------------------------------------------------------------------------- #

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from anthropic import AsyncAnthropic  # 局部 import:让该依赖保持可选
        key = api_key or os.getenv("ANTHROPIC_API_KEY") or ""
        if not key.strip():
            raise RuntimeError("ANTHROPIC_API_KEY is empty")
        self._client = AsyncAnthropic(api_key=key)
        self._model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        # Anthropic 接口要求 system 提示词单独传参,不能混在 messages 列表里。
        system_chunks = [m["content"] for m in messages if m["role"] == "system"]
        user_assistant = [m for m in messages if m["role"] in ("user", "assistant")]
        system = "\n\n".join(system_chunks) if system_chunks else None

        async with self._client.messages.stream(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=user_assistant,
        ) as stream:
            async for chunk in stream.text_stream:
                if chunk:
                    yield chunk

    # anthropic==0.39 的原生 tool_use 结构与 OpenAI 不同,暂不适配;
    # 智能体路径在该 provider 下自动禁用(走快路)。
    supports_tools = False

    async def chat_tools(self, messages, tools, tool_choice="auto", **kwargs) -> dict:
        raise NotImplementedError("anthropic provider: chat_tools not implemented; agent path disabled")


# --------------------------------------------------------------------------- #
#  豆包 / OpenAI(都走 openai SDK — ARK 兼容 OpenAI 接口)
# --------------------------------------------------------------------------- #

class OpenAICompatibleProvider:
    """通用的 OpenAI 兼容 provider;ARK 豆包正好符合这套接口形态。"""

    def __init__(self, name: str, api_key: str, base_url: str, model: str) -> None:
        from openai import AsyncOpenAI
        self.name = name
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except (IndexError, AttributeError):
                continue
            if delta:
                yield delta

    supports_tools = True

    async def chat_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice="auto",
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 6.0,
    ) -> dict:
        """非流式工具调用(智能体路径专用)。stream_chat 完全不受影响。

        - 显式 timeout,且**只对这个方法**关闭 SDK 自带重试(max_retries=0):
          智能体路径有 8 秒总预算,超时/出错应立即交给上层回退快路,
          而不是在 SDK 里静默重试把预算耗光。
        - with_options / timeout 参数在 openai 1.51 与 2.x 上签名一致。
        - TokenRouter 实测(claude-haiku-4-5):会返回 tool_calls,可能同时带前导
          content,tool_call id 形如 toolu_...,接受 role="tool" 的结果回填。
        """
        client = self._client.with_options(max_retries=0, timeout=timeout)
        kwargs: dict = {
            # AGENT_LLM_MODEL:规划器可单独用更快的模型(如 claude-haiku-4-5);
            # 不设时与流式回答同一个模型。只影响本方法。
            "model": (os.getenv("AGENT_LLM_MODEL") or "").strip() or self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        resp = await client.chat.completions.create(**kwargs)
        return _normalize_tool_response(resp)


# --------------------------------------------------------------------------- #
#  Echo — 确定性、无网络依赖,供测试使用
# --------------------------------------------------------------------------- #

class EchoProvider:
    name = "echo"

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = f"[echo] 收到「{last_user}」，已检索到候选商品，请见下方卡片。"
        for ch in reply:
            yield ch

    supports_tools = False

    async def chat_tools(self, messages, tools, tool_choice="auto", **kwargs) -> dict:
        raise NotImplementedError("echo provider has no tool calling; agent path disabled")


# --------------------------------------------------------------------------- #
#  工厂函数(Factory)
# --------------------------------------------------------------------------- #

# R10 #4.4⭐⭐ — 在进程生命周期内缓存 provider(连带缓存其 AsyncOpenAI 的
# httpx 连接池)。此前 get_provider() 每个请求都新建一个 client,导致每次
# 首 token 都要对上游重新做一次 TLS 握手。复用同一个 client 可保持连接
# 存活 → 首 token 延迟(time-to-first-token)更低也更稳定。环境变量在
# 进程内不会变化,所以单例是安全的。
_provider_singleton: "LLMProvider | None" = None


def get_provider() -> LLMProvider:
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = _build_provider()
    return _provider_singleton


def _build_provider() -> LLMProvider:
    requested = (os.getenv("LLM_PROVIDER") or "").lower().strip()
    if not requested:
        if os.getenv("TOKENROUTER_API_KEY"):
            requested = "tokenrouter"
        elif os.getenv("ANTHROPIC_API_KEY", "").strip():
            requested = "anthropic"
        elif os.getenv("DOUBAO_API_KEY"):
            requested = "doubao"
        elif os.getenv("OPENAI_API_KEY"):
            requested = "openai"
        else:
            requested = "echo"

    if requested == "anthropic":
        try:
            return AnthropicProvider()
        except Exception:
            return EchoProvider()
    if requested == "doubao":
        key = os.getenv("DOUBAO_API_KEY")
        if not key:
            return EchoProvider()
        return OpenAICompatibleProvider(
            name="doubao",
            api_key=key,
            base_url=os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3/"),
            model=os.getenv("DOUBAO_MODEL_ID", "ep-20260514111645-lmgt2"),
        )
    if requested == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return EchoProvider()
        return OpenAICompatibleProvider(
            name="openai",
            api_key=key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        )
    if requested == "tokenrouter":
        key = os.getenv("TOKENROUTER_API_KEY")
        if not key:
            return EchoProvider()
        return OpenAICompatibleProvider(
            name="tokenrouter",
            api_key=key,
            base_url=os.getenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1"),
            model=os.getenv("TOKENROUTER_MODEL", "claude-sonnet-4-6"),
        )
    return EchoProvider()
