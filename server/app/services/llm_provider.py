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
