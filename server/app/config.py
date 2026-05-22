"""Settings loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    doubao_base_url: str = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3/")
    doubao_model_id: str = os.getenv("DOUBAO_MODEL_ID", "ep-20260514111645-lmgt2")
    doubao_api_key: str = os.getenv("DOUBAO_API_KEY", "")

    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_text_collection: str = os.getenv("QDRANT_COLLECTION_TEXT", "products_text")
    qdrant_image_collection: str = os.getenv("QDRANT_COLLECTION_IMAGE", "products_image")

    server_host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    server_port: int = int(os.getenv("SERVER_PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    repo_root: Path = Path(__file__).resolve().parents[2]


settings = Settings()
