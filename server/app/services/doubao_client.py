"""Async wrapper over the Doubao (ARK) chat completions endpoint.

ARK is OpenAI-compatible, so we use the openai package with base_url
pointing at https://ark.cn-beijing.volces.com/api/v3/. The key in the
official PDF returned 401 on 2026-05-22 — confirm with the organizer
that the key in your .env is the right one.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.config import settings

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore


class DoubaoClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.doubao_api_key
        self.base_url = settings.doubao_base_url
        self.model = settings.doubao_model_id
        if AsyncOpenAI is None or not self.api_key:
            self._client = None
        else:
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    @property
    def available(self) -> bool:
        return self._client is not None

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """Stream model output token-by-token.

        Yields the text chunks the upstream emits (the deltas), so callers
        can wrap them in SSE events with whatever envelope they prefer.
        """
        if self._client is None:
            raise RuntimeError(
                "DoubaoClient not configured. Set DOUBAO_API_KEY in server/.env "
                "(get the real key from the team channel; the PDF placeholder may be wrong)."
            )

        stream = await self._client.chat.completions.create(
            model=self.model,
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

    async def chat(self, messages: list[dict]) -> str:
        """Non-streaming convenience for tests / health checks."""
        if self._client is None:
            raise RuntimeError("DoubaoClient not configured.")
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=False,
        )
        return resp.choices[0].message.content or ""
