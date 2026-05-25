from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.services.retrieval_readiness import warm_retrieval_pipeline

try:
    from fastapi.testclient import TestClient

    from app.main import create_app

    HAS_BACKEND_DEPS = True
except ModuleNotFoundError:
    TestClient = None
    create_app = None
    HAS_BACKEND_DEPS = False


class RetrievalReadinessTests(unittest.TestCase):
    @patch("app.services.rag_client.top_k")
    @patch("rag.retrieve.rerank.warmup_reranker")
    @patch("rag.retrieve.bm25.bm25_topk")
    @patch("rag.ingest.embed_text.embed_query")
    def test_warm_pipeline_initializes_retrieval_components(
        self, embed_query, bm25_topk, warmup_reranker, top_k
    ) -> None:
        with patch.dict(os.environ, {"RAG_PREWARM": "1", "RAG_RERANK": "1"}):
            status = warm_retrieval_pipeline()

        embed_query.assert_called_once()
        bm25_topk.assert_called_once()
        warmup_reranker.assert_called_once()
        top_k.assert_called_once_with("推荐适合日常使用的商品", k=5)
        self.assertEqual(status["reranker"], "ready")
        self.assertEqual(status["query_path"], "ready")

    @unittest.skipUnless(HAS_BACKEND_DEPS, "backend dependencies are not installed in this Python environment")
    @patch("app.services.retrieval_readiness.warm_retrieval_pipeline")
    def test_ready_is_exposed_after_startup_warmup(self, warm_retrieval_pipeline_mock) -> None:
        warm_retrieval_pipeline_mock.return_value = {
            "prewarm": "completed",
            "embedding": "ready",
            "bm25": "ready",
            "reranker": "ready",
            "query_path": "ready",
        }

        with TestClient(create_app()) as client:
            response = client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["retrieval"]["reranker"], "ready")

    @unittest.skipUnless(HAS_BACKEND_DEPS, "backend dependencies are not installed in this Python environment")
    @patch("app.services.retrieval_readiness.warm_retrieval_pipeline", side_effect=RuntimeError("model unavailable"))
    def test_failed_warmup_keeps_chat_unavailable(self, _warm_retrieval_pipeline_mock) -> None:
        with TestClient(create_app()) as client:
            ready_response = client.get("/ready")
            chat_response = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "推荐耳机"}]})

        self.assertEqual(ready_response.status_code, 503)
        self.assertEqual(ready_response.json()["retrieval"]["status"], "error")
        self.assertEqual(chat_response.status_code, 503)

    def test_prewarm_can_be_disabled_for_diagnostics(self) -> None:
        with patch.dict(os.environ, {"RAG_PREWARM": "0"}):
            status = warm_retrieval_pipeline()

        self.assertEqual(status["prewarm"], "disabled")


if __name__ == "__main__":
    unittest.main()
