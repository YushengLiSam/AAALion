from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class ImageURL(BaseModel):
    url: str  # accepts data:image/jpeg;base64,... or a remote URL


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURL


ContentPart = TextPart | ImagePart


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    # Either a plain string (text-only, legacy shape) or a list of content
    # parts (OpenAI-style, supports text + image_url for vision-capable models).
    content: str | list[ContentPart]


class ChatFilters(BaseModel):
    category: str | None = None
    price_max: float | None = Field(default=None, ge=0)
    exclude_brands: list[str] | None = None
