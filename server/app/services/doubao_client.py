"""Thin async wrapper over the Doubao (ARK) chat completions endpoint.

The ARK API is OpenAI-compatible, so we use the openai package with a
custom base_url. Streaming is yielded as text chunks for the SSE route.

This is a stub — Sam will fill in the real call once the token budget /
prompt structure is finalized.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.config import settings


class DoubaoClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.doubao_api_key
        if not self.api_key:
            # In dev we tolerate this; routes/chat.py uses a fixture for now.
            pass

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """Stream model output token-by-token.

        TODO(sam): implement using ``openai.AsyncOpenAI`` with
        base_url=settings.doubao_base_url and model=settings.doubao_model_id.
        Yield each ``chunk.choices[0].delta.content``.
        """
        raise NotImplementedError("Doubao client not implemented yet")
