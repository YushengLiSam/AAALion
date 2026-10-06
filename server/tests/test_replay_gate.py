"""P1 — rag/eval/replay_gate.py decision logic with in-memory stores (no models,
no Chroma/Milvus): pass when B matches or beats A against exact search, fail on
errors or on disagreements exact search cannot explain.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from rag.eval import replay_gate as rg  # noqa: E402
from rag.store.base import Hit  # noqa: E402

DIM = 8
N_TEXT, N_IMG = 80, 20


class MemStore:
    """Exact cosine search over fixed vectors, with optional sabotage."""

    def __init__(self, backend, text, image, *, drop_rank=None, fail=False):
        self.backend = backend
        self.text, self.image = text, image
        self.drop_rank = drop_rank  # remove the hit at this rank (simulates a worse ANN)
        self.fail = fail

    def _search(self, data, vec, k, where=None):
        if self.fail:
            raise ConnectionError("down")
        ids, mat, metas = data
        q = np.asarray(vec, dtype=np.float64)
        sims = mat @ (q / np.linalg.norm(q))
        order = [i for i in np.argsort(-sims, kind="stable") if rg_match(metas[i], where)]
        hits = [Hit(ids[i], float(sims[i]), metas[i]) for i in order[: k + 1]]
        if self.drop_rank is not None and len(hits) > self.drop_rank:
            hits.pop(self.drop_rank)
        return hits[:k]

    def query_text(self, vec, k=5, *, where=None):
        return self._search(self.text, vec, k, where)

    def query_image(self, vec, k=5):
        return self._search(self.image, vec, k)

    def text_count(self):
        return len(self.text[0])

    def image_count(self):
        return len(self.image[0])

    def filterable_fields(self, collection=None):
        return frozenset({"brand_country", "currency", "category"})

    def export(self, collection, batch_size=1000):
        ids, mat, metas = self.text if collection == "products_text" else self.image
        yield list(ids), mat.tolist(), list(metas), [None] * len(ids)

    def schema_info(self):
        return {"backend": self.backend, "text": {"count": self.text_count(), "schema_version": 2},
                "image": {"count": self.image_count(), "schema_version": 2}}


def rg_match(meta, where):
    from rag.eval.store_parity import _match

    return _match(meta, where)


def _data(n, seed, prefix):
    rng = np.random.default_rng(seed)
    mat = rng.normal(size=(n, DIM))
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    ids = [f"{prefix}{i}" for i in range(n)]
    metas = [{"product_id": ids[i], "category": "A" if i % 2 else "B"} for i in range(n)]
    return ids, mat, metas


TEXT = _data(N_TEXT, 1, "t")
IMG = _data(N_IMG, 2, "p")
CALLS = [{"set": "golden.jsonl", "query": f"q{i}", "filter": "none", "where": None} for i in range(10)] + [
    {"set": "golden.jsonl", "query": f"q{i}", "filter": "prod", "where": {"category": "A"}} for i in range(10)
]


def _fake_embed(text):
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    return rng.normal(size=DIM).tolist()


class ReplayGateTests(unittest.TestCase):
    def _run(self, a, b, *extra):
        out = Path(tempfile.mkdtemp()) / "r.json"
        stores = {"chroma": a, "milvus": b}
        with patch.object(rg, "_store", side_effect=lambda name, uri: stores[name]), \
                patch.object(rg, "build_text_calls", return_value=[dict(c) for c in CALLS]), \
                patch("rag.ingest.embed_text.embed_query", side_effect=_fake_embed), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = rg.main(["--out", str(out), "--min-calls", "100", "--k", "10", *extra])
        return rc, json.loads(out.read_text(encoding="utf-8"))

    def test_identical_stores_pass_with_enough_calls(self) -> None:
        rc, rep = self._run(MemStore("chroma", TEXT, IMG), MemStore("milvus", TEXT, IMG))
        self.assertEqual(rc, 0, rep["reasons"])
        self.assertEqual(rep["verdict"], "PASS")
        self.assertGreaterEqual(rep["counts"]["calls"], 100)  # whole rounds until >= --min-calls
        self.assertEqual(rep["counts"]["calls"] % (len(CALLS) + N_IMG), 0)
        self.assertEqual(rep["agreement"]["top10_agreement"], 1.0)
        self.assertEqual(set(rep["slices"]), {"text_none", "text_prod_filter", "image"})
        for store in ("a", "b", "exact"):
            self.assertIsNotNone(rep["latency_ms"][store]["all"]["p95_ms"])

    def test_worse_candidate_fails_as_unexplained(self) -> None:
        rc, rep = self._run(MemStore("chroma", TEXT, IMG), MemStore("milvus", TEXT, IMG, drop_rank=3))
        self.assertEqual(rc, 1)
        self.assertGreater(rep["agreement"]["unexplained"], 0)
        self.assertEqual(rep["agreement"]["explained_by_exact"], 0)

    def test_better_candidate_is_explained_by_exact_search(self) -> None:
        # A is the approximate one; B is exact → every disagreement is B being closer to exact
        rc, rep = self._run(MemStore("chroma", TEXT, IMG, drop_rank=3), MemStore("milvus", TEXT, IMG))
        self.assertEqual(rc, 0, rep["reasons"])
        self.assertLess(rep["agreement"]["top10_agreement"], 0.99)
        self.assertEqual(rep["agreement"]["agreement_incl_explained"], 1.0)
        self.assertGreater(rep["agreement"]["b_better_than_a_vs_exact"], 0)

    def test_any_error_fails(self) -> None:
        rc, rep = self._run(MemStore("chroma", TEXT, IMG), MemStore("milvus", TEXT, IMG, fail=True))
        self.assertEqual(rc, 1)
        self.assertGreater(rep["errors"]["count"], 0)
        self.assertIn("down", rep["errors"]["sample"][0]["error"])

    def test_p95_budget_is_optional(self) -> None:
        rc, rep = self._run(MemStore("chroma", TEXT, IMG), MemStore("milvus", TEXT, IMG), "--p95-max-ms", "0.000001")
        self.assertEqual(rc, 1)
        self.assertTrue(any("p95" in r for r in rep["reasons"]))

    def test_percentile_helper(self) -> None:
        self.assertEqual(rg._pct([1, 2, 3, 4, 100], 50), 3)
        self.assertEqual(rg._pct([1, 2, 3, 4, 100], 95), 100)
        self.assertIsNone(rg._pct([], 95))


if __name__ == "__main__":
    unittest.main()
