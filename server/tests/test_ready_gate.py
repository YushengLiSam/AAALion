"""P0.4 — vector-store readiness gate, /ready status codes and the
dense→keyword fallback counter.

The autodeploy guard is `curl -sf $READY_URL | grep -q ready`: `-f` makes curl
print nothing on any HTTP status >= 400, so "not ready" MUST be non-2xx. These
tests pin that, with fakes only (no models, no Chroma/Milvus).
"""

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

from app.services import retrieval_readiness as rr  # noqa: E402
from rag.store.base import TEXT_SCALAR_FIELDS, IMAGE_SCALAR_FIELDS, Hit  # noqa: E402

try:
    from fastapi.testclient import TestClient

    from app.main import create_app

    HAS_BACKEND_DEPS = True
except ModuleNotFoundError:  # pragma: no cover
    TestClient = None
    create_app = None
    HAS_BACKEND_DEPS = False


def _vec(_text):
    return [0.1, 0.2, 0.3]


class GateStore:
    """Configurable fake of a raw VectorStore."""

    def __init__(self, backend="chroma", text=1082, image=145, schema=None, fields=None,
                 text_hits=True, image_hits=True, query_error=None, count_error=None):
        self.backend = backend
        self._text, self._image = text, image
        self._schema = schema
        self._fields = fields
        self._text_hits, self._image_hits = text_hits, image_hits
        self._query_error, self._count_error = query_error, count_error
        self.queries = []

    def text_count(self):
        if self._count_error:
            raise self._count_error
        return self._text

    def image_count(self):
        if self._count_error:
            raise self._count_error
        return self._image

    def query_text(self, embedding, k=5, *, where=None):
        self.queries.append(("text", list(embedding), k, where))
        if self._query_error:
            raise self._query_error
        return [Hit("t1", 0.9, {"product_id": "p1"})] if self._text_hits and self._text else []

    def query_image(self, embedding, k=5):
        self.queries.append(("image", list(embedding), k, None))
        if self._query_error:
            raise self._query_error
        return [Hit("p1", 1.0, {"product_id": "p1"})] if self._image_hits and self._image else []

    def export(self, collection, batch_size=1000):
        if self._image:
            yield ["p1"], [[0.5, 0.5]], [{"product_id": "p1"}], [None]

    def schema_info(self):
        def entry(declared, n):
            fields = sorted(declared) if self._fields is None else sorted(self._fields)
            return {"count": n, "schema_version": self._schema, "filterable_fields": fields}

        return {"backend": self.backend, "text": entry(TEXT_SCALAR_FIELDS, self._text),
                "image": entry(IMAGE_SCALAR_FIELDS, self._image)}


class VectorStoreGateTests(unittest.TestCase):
    def test_chroma_legacy_index_passes(self) -> None:
        # production today: Chroma, schema_version null, 1082 / 145
        store = GateStore("chroma", schema=None, fields=["product_id", "category", "brand"])
        res = rr.vector_store_gate(store, embed=_vec)
        self.assertTrue(res["ok"], res["reasons"])
        self.assertEqual(res["checks"]["text_count"], 1082)
        self.assertEqual(res["checks"]["image_hits"], 1)
        # it really queried the store with the embedding and a stored image vector
        self.assertEqual(store.queries[0][:2], ("text", [0.1, 0.2, 0.3]))
        self.assertEqual(store.queries[1][:2], ("image", [0.5, 0.5]))

    def test_milvus_v2_passes_and_pre_v2_fails(self) -> None:
        self.assertTrue(rr.vector_store_gate(GateStore("milvus", schema=2), embed=_vec)["ok"])
        res = rr.vector_store_gate(GateStore("milvus", schema=None, fields=[]), embed=_vec)
        self.assertFalse(res["ok"])
        self.assertTrue(any("schema_version" in r for r in res["reasons"]))
        # schema_version 2 but v2 columns missing (e.g. a partial migration) also fails
        res = rr.vector_store_gate(GateStore("milvus", schema=2, fields=["product_id"]), embed=_vec)
        self.assertFalse(res["ok"])

    def test_empty_collections_fail(self) -> None:
        res = rr.vector_store_gate(GateStore(text=0), embed=_vec)
        self.assertFalse(res["ok"])
        self.assertTrue(any("text collection has 0" in r for r in res["reasons"]))
        res = rr.vector_store_gate(GateStore(image=0), embed=_vec)
        self.assertFalse(res["ok"])
        self.assertTrue(any("image collection has 0" in r for r in res["reasons"]))

    def test_image_requirement_can_be_relaxed(self) -> None:
        with patch.dict(os.environ, {"RAG_READY_GATE_REQUIRE_IMAGE": "0"}):
            self.assertTrue(rr.vector_store_gate(GateStore(image=0), embed=_vec)["ok"])

    def test_min_counts(self) -> None:
        with patch.dict(os.environ, {"RAG_READY_MIN_TEXT": "2000"}):
            self.assertFalse(rr.vector_store_gate(GateStore(), embed=_vec)["ok"])

    def test_no_hits_or_errors_fail_without_raising(self) -> None:
        self.assertFalse(rr.vector_store_gate(GateStore(text_hits=False), embed=_vec)["ok"])
        res = rr.vector_store_gate(GateStore(query_error=ConnectionError("milvus:19530 refused")), embed=_vec)
        self.assertFalse(res["ok"])
        self.assertTrue(any("refused" in r for r in res["reasons"]))
        res = rr.vector_store_gate(GateStore(count_error=RuntimeError("boom")), embed=_vec)
        self.assertFalse(res["ok"])

        def bad_embed(_):
            raise OSError("model missing")

        self.assertFalse(rr.vector_store_gate(GateStore(), embed=bad_embed)["ok"])

    def test_gate_bypasses_the_fallback_wrapper(self) -> None:
        """A FallbackStore answering from Chroma must not hide a dead Milvus from the gate."""
        import datetime as dt

        from rag.store.composite import FallbackStore

        dead = GateStore("milvus", schema=2, query_error=ConnectionError("down"))
        healthy = GateStore("chroma")
        wrapped = FallbackStore(dead, lambda: healthy, fallback_name="chroma",
                                until=dt.date.today() + dt.timedelta(days=3))
        with patch("sys.stderr"):
            self.assertEqual(len(wrapped.query_text([0.1], 5)), 1)  # user path is served...
        with patch("rag.store.get_store", return_value=wrapped):
            res = rr.vector_store_gate(embed=_vec)
        self.assertEqual(res["backend"], "milvus")
        self.assertFalse(res["ok"])  # ...but the gate still sees the primary is down


class GateCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        rr.reset_gate_cache()

    def tearDown(self) -> None:
        rr.reset_gate_cache()

    def test_cached_for_ttl_and_force_refreshes(self) -> None:
        results = iter([{"ok": True, "reasons": []}, {"ok": False, "reasons": ["x"]}])
        with patch.object(rr, "vector_store_gate", side_effect=lambda: next(results)) as gate:
            self.assertTrue(rr.gate_status(ttl_s=60)["ok"])
            self.assertTrue(rr.gate_status(ttl_s=60)["ok"])
            self.assertEqual(gate.call_count, 1)
            self.assertFalse(rr.gate_status(force=True)["ok"])
            self.assertEqual(gate.call_count, 2)

    def test_concurrent_check_returns_pending_instead_of_piling_up(self) -> None:
        self.assertTrue(rr._gate_lock.acquire(blocking=False))
        try:
            res = rr.gate_status(ttl_s=0)
        finally:
            rr._gate_lock.release()
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("pending"))

    def test_mode_parsing(self) -> None:
        for raw, want in (("", "off"), ("report", "report"), ("ENFORCE", "enforce"), ("bogus", "off")):
            with patch.dict(os.environ, {"RAG_READY_GATE": raw}):
                self.assertEqual(rr.gate_mode(), want)


@unittest.skipUnless(HAS_BACKEND_DEPS, "backend dependencies are not installed in this Python environment")
class ReadyRouteTests(unittest.TestCase):
    WARM = {"prewarm": "completed", "embedding": "ready", "bm25": "ready", "reranker": "ready", "query_path": "ready"}

    def _get_ready(self, mode: str, gate: dict):
        with patch.dict(os.environ, {"RAG_READY_GATE": mode}), \
                patch("app.services.retrieval_readiness.warm_retrieval_pipeline", return_value=dict(self.WARM)), \
                patch("app.services.retrieval_readiness.gate_status", return_value=gate) as gs:
            with TestClient(create_app()) as client:
                resp = client.get("/ready")
        return resp, gs

    def test_enforce_returns_503_when_gate_fails(self) -> None:
        resp, _ = self._get_ready("enforce", {"ok": False, "reasons": ["text query failed: refused"]})
        self.assertEqual(resp.status_code, 503)  # curl -sf → no output → autodeploy rolls back
        self.assertGreaterEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["reason"], "vector_store_gate")
        self.assertIn("refused", body["vector_store_gate"]["reasons"][0])
        self.assertIn("dense_to_keyword", body["fallbacks"])

    def test_enforce_returns_200_when_gate_passes(self) -> None:
        resp, gs = self._get_ready("enforce", {"ok": True, "reasons": [], "backend": "chroma"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")
        self.assertEqual(resp.json()["vector_store_gate"]["mode"], "enforce")
        self.assertTrue(gs.called)

    def test_report_mode_never_changes_the_status_code(self) -> None:
        resp, _ = self._get_ready("report", {"ok": False, "reasons": ["x"]})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["vector_store_gate"]["ok"])

    def test_off_by_default_keeps_the_old_response(self) -> None:
        resp, gs = self._get_ready("", {"ok": False, "reasons": ["x"]})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("vector_store_gate", resp.json())
        self.assertFalse(gs.called)
        self.assertEqual(resp.json()["retrieval"]["reranker"], "ready")

    def test_slow_gate_times_out_as_not_ready(self) -> None:
        import time as _time

        def slow():
            _time.sleep(0.5)
            return {"ok": True, "reasons": []}

        with patch.dict(os.environ, {"RAG_READY_GATE": "enforce", "RAG_READY_GATE_TIMEOUT_S": "0.05"}), \
                patch("app.services.retrieval_readiness.warm_retrieval_pipeline", return_value=dict(self.WARM)), \
                patch("app.services.retrieval_readiness.gate_status", side_effect=slow):
            with TestClient(create_app()) as client:
                resp = client.get("/ready")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("timed out", resp.json()["vector_store_gate"]["reasons"][0])

    def test_warmup_failure_is_still_503(self) -> None:
        with patch.dict(os.environ, {"RAG_READY_GATE": "enforce"}), \
                patch("app.services.retrieval_readiness.warm_retrieval_pipeline", side_effect=RuntimeError("x")):
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/ready").status_code, 503)


class FallbackCounterTests(unittest.TestCase):
    def test_dense_failure_increments_the_counter(self) -> None:
        from rag.retrieve import query as q

        before = q.fallback_stats()["dense_to_keyword"]
        with patch("rag.ingest.embed_text.embed_query", return_value=[0.1, 0.2]), \
                patch("rag.store.query_text", side_effect=ConnectionError("milvus down")), \
                patch("sys.stderr"):
            hits = q.query("降噪耳机", k=3)
        self.assertLessEqual(len(hits), 3)  # keyword fallback still answers
        stats = q.fallback_stats()
        self.assertEqual(stats["dense_to_keyword"], before + 1)
        self.assertIn("milvus down", stats["last"]["error"])

    def test_image_failure_is_counted(self) -> None:
        from rag.retrieve import query as q

        before = q.fallback_stats()["image_query_failed"]
        with patch("rag.ingest.embed_image.embed_image_bytes", return_value=[0.1]), \
                patch("rag.store.query_image", side_effect=RuntimeError("down")), \
                patch("sys.stderr"):
            self.assertEqual(q.query_image(b"\x89PNG", k=1), [])
        self.assertEqual(q.fallback_stats()["image_query_failed"], before + 1)

    def test_degradation_stats_never_raises(self) -> None:
        stats = rr.degradation_stats()
        self.assertIn("dense_to_keyword", stats)


if __name__ == "__main__":
    unittest.main()
