"""Settings loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    # server/app/config.py → parents[1] = server/ ; the canonical .env lives there.
    # Also load repo-root .env if present (legacy / convenience).
    _server_root = Path(__file__).resolve().parents[1]
    load_dotenv(_server_root / ".env")
    load_dotenv(_server_root.parent / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    doubao_base_url: str = os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3/")
    doubao_model_id: str = os.getenv("DOUBAO_MODEL_ID", "ep-20260514111645-lmgt2")
    doubao_api_key: str = os.getenv("DOUBAO_API_KEY", "")

    # P0.9 清理:删掉了从未被读取的 qdrant_* 和 server_host / server_port。
    # 向量库由环境变量 RAG_STORE 选择(chroma / milvus),见 rag/store/__init__.py;
    # 监听地址 / 端口只由 uvicorn 命令行决定(线上见 deploy/systemd/lionpick.service.d/30-bind-localhost.conf)。
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    repo_root: Path = Path(__file__).resolve().parents[2]


settings = Settings()
