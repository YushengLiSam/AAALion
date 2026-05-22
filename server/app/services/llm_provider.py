"""Pluggable LLM provider abstraction.

Picks one of:
  - ``anthropic`` — Claude API (default while Doubao key is being re-issued).
  - ``doubao``    — ARK / Volcengine, OpenAI-compatible.
  - ``openai``    — OpenAI API.
  - ``echo``      — no-network deterministic provider for tests.

Selection via ``LLM_PROVIDER`` env (default: anthropic if ANTHROPIC_API_KEY
is set, else echo).
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Protocol


class LLMProvider(Protocol):
    name: str

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:  # noqa: D401
        """Yield response text chunks."""
        ...


# --------------------------------------------------------------------------- #
#  Anthropic Claude (default during Doubao outage)
# --------------------------------------------------------------------------- #

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from anthropic import AsyncAnthropic  # local import: keep optional
        key = api_key or os.getenv("ANTHROPIC_API_KEY") or ""
        if not key.strip():
            raise RuntimeError("ANTHROPIC_API_KEY is empty")
        self._client = AsyncAnthropic(api_key=key)
        self._model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        # Anthropic wants system separated from the messages list.
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
#  Doubao / OpenAI (both via openai SDK — ARK is OpenAI-compatible)
# --------------------------------------------------------------------------- #

class OpenAICompatibleProvider:
    """Generic OpenAI-compatible provider; ARK Doubao fits this shape."""

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
#  Echo — deterministic, no-network, for tests
# --------------------------------------------------------------------------- #

class EchoProvider:
    name = "echo"

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = f"[echo] 收到「{last_user}」，已检索到候选商品，请见下方卡片。"
        for ch in reply:
            yield ch


# --------------------------------------------------------------------------- #
#  Factory
# --------------------------------------------------------------------------- #

def get_provider() -> LLMProvider:
    requested = (os.getenv("LLM_PROVIDER") or "").lower().strip()
    if not requested:
        if os.getenv("ANTHROPIC_API_KEY"):
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
    return EchoProvider()
