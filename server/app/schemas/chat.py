from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatFilters(BaseModel):
    category: str | None = None
    price_max: float | None = Field(default=None, ge=0)
    exclude_brands: list[str] | None = None
