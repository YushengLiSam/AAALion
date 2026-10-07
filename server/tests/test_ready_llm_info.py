"""/ready 暴露当前生效的 LLM provider / 模型(不含任何机密)+ TokenRouter 默认模型。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.services import llm_provider  # noqa: E402

try:
    from fastapi.testclient import TestClient

    from app.main import create_app

    HAS_BACKEND_DEPS = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_BACKEND_DEPS = False

WARM = {"prewarm": "completed", "embedding": "ready", "bm25": "ready", "reranker": "ready", "query_path": "ready"}
FAKE_KEY = "fake-test-key-not-a-secret"


class _ResetProvider:
    def setUp(self) -> None:
        self._saved = llm_provider._provider_singleton
        llm_provider._provider_singleton = None

    def tearDown(self) -> None:
        llm_provider._provider_singleton = self._saved


class DefaultModelTests(_ResetProvider, unittest.TestCase):
    def test_tokenrouter_default_model_is_haiku(self) -> None:
        env = {"LLM_PROVIDER": "tokenrouter", "TOKENROUTER_API_KEY": FAKE_KEY}
        with patch.dict(os.environ, env):
            os.environ.pop("TOKENROUTER_MODEL", None)
            p = llm_provider._build_provider()
        self.assertEqual(p.name, "tokenrouter")
        self.assertEqual(p._model, "claude-haiku-4-5")

    def test_env_example_matches_code_default(self) -> None:
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("TOKENROUTER_MODEL=claude-haiku-4-5", text)
        self.assertNotIn("QDRANT", text)


@unittest.skipUnless(HAS_BACKEND_DEPS, "backend dependencies are not installed in this Python environment")
class ReadyLlmInfoTests(_ResetProvider, unittest.TestCase):
    def _ready(self, env: dict):
        with patch.dict(os.environ, {"RAG_READY_GATE": "", **env}), \
                patch("app.services.retrieval_readiness.warm_retrieval_pipeline", return_value=dict(WARM)):
            with TestClient(create_app()) as client:
                return client.get("/ready")

    def test_ready_shows_effective_provider_and_model_without_secrets(self) -> None:
        resp = self._ready({"LLM_PROVIDER": "tokenrouter", "TOKENROUTER_API_KEY": FAKE_KEY,
                            "TOKENROUTER_MODEL": "claude-haiku-4-5", "AGENT_LLM_MODEL": "claude-haiku-4-5"})
        self.assertEqual(resp.status_code, 200)
        llm = resp.json()["llm"]
        self.assertEqual(llm, {"requested": "tokenrouter", "provider": "tokenrouter",
                               "model": "claude-haiku-4-5", "agent_model": "claude-haiku-4-5"})
        self.assertNotIn(FAKE_KEY, resp.text)
        self.assertNotIn("api.tokenrouter.com", resp.text)

    def test_missing_key_is_visible_as_echo(self) -> None:
        resp = self._ready({"LLM_PROVIDER": "tokenrouter", "TOKENROUTER_API_KEY": ""})
        llm = resp.json()["llm"]
        self.assertEqual(llm["requested"], "tokenrouter")
        self.assertEqual(llm["provider"], "echo")
        self.assertIsNone(llm["model"])

    def test_provider_error_does_not_break_ready(self) -> None:
        with patch("app.services.llm_provider.get_provider", side_effect=RuntimeError("boom")):
            resp = self._ready({})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["llm"]["provider"], "unavailable")


if __name__ == "__main__":
    unittest.main()
