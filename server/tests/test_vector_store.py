"""R15 — vector-store abstraction: filter translation, backend contracts,
legacy-index capability detection, Milvus filtered-search strategy, filter
pushdown gating and the migration guard.

Milvus tests are skipped when pymilvus / milvus-lite are not installed (the
default deployment does not need them).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("CHROMA_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import numpy as np  # noqa: E402

from rag.retrieve.query import Filter, _build_where, product_matches_filter  # noqa: E402
from rag.store import TEXT_COLLECTION, Doc, get_store  # noqa: E402
from rag.store.chroma_store import ChromaStore  # noqa: E402
from rag.store.filters import UnsupportedFilter, fields_in_where, to_milvus_expr, validate_where  # noqa: E402

HAS_MILVUS = importlib.util.find_spec("pymilvus") is not None and importlib.util.find_spec("milvus_lite") is not None
# In-process Milvus writes use FLAT on macOS: this pytest process may already hold
# torch's OpenMP runtime, and building a faiss HNSW index here would abort the
# whole test run (see MilvusStore.write_needs_isolation). HNSW builds are covered
# in a clean child process by LoaderSubprocessTests.
IN_PROCESS_INDEX = "FLAT" if sys.platform == "darwin" else "HNSW"


def _docs(n: int, dim: int = 8, seed: int = 7):
    rng = np.random.default_rng(seed)
    vecs = rng.normal(size=(n, dim)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    docs = []
    for i in range(n):
        cat = "耳机" if i % 25 == 0 else "面霜"  # 4% selectivity
        docs.append(
            Doc(
                id=f"p{i}::desc::{i}",
                text=f"商品 {i}",
                metadata={
                    "product_id": f"p{i}",
                    "chunk_type": "desc",
                    "category": "美妆护肤" if cat == "面霜" else "数码电子",
                    "sub_category": cat,
                    "brand": f"品牌{i % 7}",
                    "brand_country": ["JP", "US", "CN", "FR"][i % 4],
                    "currency": "CNY",
                    "base_price": float(50 + i),
                    "text": f"商品 {i}",
                    **({"rating": 5} if i % 2 else {}),
                },
            )
        )
    return docs, vecs


def _exact(vecs: np.ndarray, docs, q: np.ndarray, k: int, pred=lambda m: True) -> list[str]:
    idx = [i for i, d in enumerate(docs) if pred(d.metadata)]
    sims = vecs[idx] @ (q / np.linalg.norm(q))
    order = np.argsort(-sims, kind="stable")[:k]
    return [docs[idx[j]].id for j in order]


class FilterTranslatorTests(unittest.TestCase):
    def test_equality_shorthand_and_list_ops(self) -> None:
        self.assertEqual(to_milvus_expr({"category": "美妆护肤"}), 'category == "美妆护肤"')
        self.assertEqual(to_milvus_expr({"brand": {"$in": ["资生堂", "SK-II"]}}), 'brand in ["资生堂", "SK-II"]')
        self.assertEqual(to_milvus_expr({"brand_country": {"$nin": ["JP"]}}), 'brand_country not in ["JP"]')

    def test_comparison_and_nested_logic(self) -> None:
        where = {
            "$and": [
                {"sub_category": {"$in": ["面霜"]}},
                {"$or": [{"$and": [{"currency": "CNY"}, {"base_price": {"$lte": 2000}}]}, {"currency": {"$ne": "CNY"}}]},
            ]
        }
        self.assertEqual(
            to_milvus_expr(where),
            '(sub_category in ["面霜"]) and (((currency == "CNY") and (base_price <= 2000)) or (currency != "CNY"))',
        )

    def test_literals_are_escaped_and_typed(self) -> None:
        self.assertEqual(to_milvus_expr({"brand": 'a"b'}), 'brand == "a\\"b"')
        self.assertEqual(to_milvus_expr({"base_price": {"$gt": 9.5}}), "base_price > 9.5")
        self.assertEqual(to_milvus_expr({"flag": True}), "flag == true")

    def test_rejects_anything_outside_the_subset(self) -> None:
        for bad in (
            {"brand": {"$in": []}},
            {"brand": {"$regex": "x"}},
            {"bad field": "x"},
            {"base_price": {"$gt": float("nan")}},
            {"base_price": {"$gt": 1, "$lt": 5}},
            {"$not": {"brand": "x"}},
            {"$and": []},
        ):
            with self.subTest(bad=bad), self.assertRaises(UnsupportedFilter):
                to_milvus_expr(bad)

    def test_every_production_filter_is_translatable(self) -> None:
        filters = [
            Filter(category="美妆护肤"),
            Filter(sub_categories=["面霜", "精华"], brand_exclude=["雅诗兰黛"]),
            Filter(brand_include=["Apple"], price_max_cny=2000),
            Filter(price_min_cny=100, price_max_cny=500),
        ]
        for f in filters:
            with self.subTest(f=f):
                validate_where(_build_where(f))

    def test_fields_in_where(self) -> None:
        where = {"$and": [{"category": "x"}, {"$or": [{"brand": {"$in": ["a"]}}, {"currency": {"$ne": "CNY"}}]}]}
        self.assertEqual(fields_in_where(where), {"category", "brand", "currency"})


class StoreFactoryTests(unittest.TestCase):
    def test_unknown_backend_is_an_error_not_a_silent_fallback(self) -> None:
        with self.assertRaises(ValueError):
            get_store("qdrant")

    def test_default_backend_is_chroma(self) -> None:
        with patch.dict(os.environ, {"RAG_STORE": ""}):
            self.assertEqual(get_store().backend, "chroma")


class ChromaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = ChromaStore(self.tmp)
        self.docs, self.vecs = _docs(120)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_contract_upsert_query_filter_count_reset(self) -> None:
        self.store.upsert_text(self.docs, self.vecs)
        self.assertEqual(self.store.text_count(), 120)
        q = self.vecs[3]
        hits = self.store.query_text(q, 5)
        self.assertEqual(hits[0].id, self.docs[3].id)
        self.assertAlmostEqual(hits[0].score, 1.0, places=4)
        filtered = self.store.query_text(q, 50, where={"brand_country": {"$nin": ["JP"]}})
        self.assertTrue(filtered and all(h.metadata["brand_country"] != "JP" for h in filtered))
        self.store.reset_collection(TEXT_COLLECTION)
        self.assertEqual(self.store.text_count(), 0)

    def test_fresh_index_is_stamped_v2(self) -> None:
        self.store.upsert_text(self.docs, self.vecs)
        self.assertEqual(self.store.schema_version(), 2)
        self.assertIn("brand_country", self.store.filterable_fields())

    def test_legacy_index_is_detected_from_its_documents(self) -> None:
        import chromadb

        client = chromadb.PersistentClient(path=self.tmp)
        col = client.get_or_create_collection(TEXT_COLLECTION, metadata={"hnsw:space": "cosine"})
        legacy = [{k: v for k, v in d.metadata.items() if k not in ("brand_country", "currency")} for d in self.docs]
        col.upsert(ids=[d.id for d in self.docs], embeddings=self.vecs.tolist(), metadatas=legacy)
        store = ChromaStore(self.tmp)
        self.assertIsNone(store.schema_version())
        fields = store.filterable_fields()
        self.assertNotIn("brand_country", fields)
        self.assertNotIn("currency", fields)
        self.assertIn("brand", fields)

    def test_properties_are_written_at_creation_and_keep_cosine(self) -> None:
        self.store.reset_collection(TEXT_COLLECTION, properties={"origin_fp": "abc"})
        self.store.upsert_text(self.docs, self.vecs)
        self.assertEqual(self.store.index_properties(), {"origin_fp": "abc"})
        self.assertEqual(self.store.schema_version(), 2)
        hit = self.store.query_text(self.vecs[9], 1)[0]
        self.assertAlmostEqual(hit.score, 1.0, places=4)  # still cosine

    def test_schema_info_does_not_expose_absolute_paths(self) -> None:
        self.store.upsert_text(self.docs, self.vecs)
        self.assertFalse(Path(self.store.schema_info()["path"]).is_absolute())

    def test_export_round_trips_vectors(self) -> None:
        self.store.upsert_text(self.docs, self.vecs)
        ids, vecs = [], []
        for i, v, _, _ in self.store.export(TEXT_COLLECTION, batch_size=50):
            ids += i
            vecs += v
        self.assertEqual(len(ids), 120)
        got = dict(zip(ids, vecs))
        np.testing.assert_allclose(got[self.docs[5].id], self.vecs[5], atol=1e-6)


@unittest.skipUnless(HAS_MILVUS, "pymilvus / milvus-lite not installed")
class MilvusStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        from rag.store.milvus_store import MilvusStore

        self.tmp = tempfile.mkdtemp()
        self.store = MilvusStore(uri=str(Path(self.tmp) / "t.db"), index_type=IN_PROCESS_INDEX)
        self.docs, self.vecs = _docs(400)
        self.store.upsert_text(self.docs, self.vecs)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_contract_and_metadata_round_trip(self) -> None:
        self.assertEqual(self.store.text_count(), 400)
        hit = self.store.query_text(self.vecs[11], 1)[0]
        self.assertEqual(hit.id, self.docs[11].id)
        self.assertAlmostEqual(hit.score, 1.0, places=4)
        # declared columns come back as-is; undeclared keys (rating) are flattened back from JSON
        self.assertEqual(hit.metadata["product_id"], "p11")
        self.assertEqual(hit.metadata["brand_country"], self.docs[11].metadata["brand_country"])
        self.assertEqual(hit.metadata["rating"], 5)
        self.assertEqual(hit.metadata["text"], "商品 11")

    def test_schema_declares_filterable_fields(self) -> None:
        fields = self.store.filterable_fields()
        for f in ("brand_country", "currency", "category", "sub_category", "brand", "base_price"):
            self.assertIn(f, fields)

    def test_selective_filter_takes_exact_path_and_misses_nothing(self) -> None:
        # 4% of docs match; plain HNSW with a bounded ef is exactly the case that under-returns
        pred = lambda m: m["sub_category"] == "耳机"  # noqa: E731
        want_n = sum(1 for d in self.docs if pred(d.metadata))
        for qi in (0, 17, 333):
            q = self.vecs[qi]
            hits = self.store.query_text(q, 60, where={"sub_category": {"$in": ["耳机"]}})
            self.assertTrue(self.store.last_search_path.startswith("exact"))
            self.assertEqual(len(hits), want_n)
            self.assertEqual([h.id for h in hits], _exact(self.vecs, self.docs, q, 60, pred))

    def test_stale_count_cache_falls_back_to_hnsw_and_stays_correct(self) -> None:
        where = {"brand_country": {"$nin": ["JP"]}}  # 300 of 400 match
        from rag.store.filters import to_milvus_expr

        self.store._count_cache[("products_text", to_milvus_expr(where))] = (10**12, 5)  # pretend: only 5 match
        with patch("rag.store.milvus_store._BRUTE_MAX", 10):
            hits = self.store.query_text(self.vecs[2], 20, where=where)
        self.assertTrue(self.store.last_search_path.startswith("hnsw(m=300"))
        self.assertEqual(len(hits), 20)
        self.assertTrue(all(h.metadata["brand_country"] != "JP" for h in hits))

    def test_filter_matching_nothing_returns_empty(self) -> None:
        self.assertEqual(self.store.query_text(self.vecs[0], 5, where={"brand": "不存在"}), [])
        self.assertEqual(self.store.last_search_path, "empty")

    def test_broad_filter_uses_hnsw_with_scaled_ef(self) -> None:
        with patch("rag.store.milvus_store._BRUTE_MAX", 10):
            hits = self.store.query_text(self.vecs[2], 10, where={"brand_country": {"$nin": ["JP"]}})
        self.assertTrue(self.store.last_search_path.startswith("hnsw(m="))
        self.assertTrue(hits and all(h.metadata["brand_country"] != "JP" for h in hits))

    def test_properties_round_trip(self) -> None:
        self.store.reset_collection(TEXT_COLLECTION, properties={"origin_fp": "xyz"})
        self.assertEqual(self.store.index_properties(), {})  # created lazily on first write
        self.store.upsert_text(self.docs, self.vecs)
        self.assertEqual(self.store.index_properties(), {"origin_fp": "xyz"})

    def test_reloads_after_the_collection_is_released_elsewhere(self) -> None:
        # e.g. the milvus-lite server restarted: collections reopen as "released"
        self.assertEqual(self.store.query_text(self.vecs[5], 1)[0].id, self.docs[5].id)
        self.store._client().release_collection(TEXT_COLLECTION)
        self.assertEqual(self.store.query_text(self.vecs[6], 1)[0].id, self.docs[6].id)
        self.assertEqual(self.store.text_count(), 400)
        hits = self.store.query_text(self.vecs[0], 60, where={"sub_category": {"$in": ["耳机"]}})
        self.assertTrue(hits)

    def test_export_and_reset(self) -> None:
        n = sum(len(i) for i, _, _, _ in self.store.export(TEXT_COLLECTION, batch_size=128))
        self.assertEqual(n, 400)
        self.store.reset_collection(TEXT_COLLECTION)
        self.assertEqual(self.store.text_count(), 0)
        self.assertEqual(self.store.query_text(self.vecs[0], 5), [])

    def test_varchar_columns_truncate_on_utf8_bytes(self) -> None:
        from rag.store.milvus_store import _truncate_utf8

        self.assertEqual(_truncate_utf8("资生堂", 7), "资生")  # 3 bytes per CJK char
        self.assertEqual(_truncate_utf8("abc", 10), "abc")


@unittest.skipUnless(HAS_MILVUS, "pymilvus / milvus-lite not installed")
class MilvusWriteGuardTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "the OpenMP double-runtime abort is macOS-specific")
    def test_guard_raises_instead_of_aborting_the_process(self) -> None:
        from rag.store.milvus_store import MilvusStore

        tmp = tempfile.mkdtemp()
        try:
            store = MilvusStore(uri=str(Path(tmp) / "g.db"), index_type="HNSW")
            docs, vecs = _docs(20)
            fake = {} if "torch" in sys.modules else {"torch": types.ModuleType("torch")}
            with patch.dict(sys.modules, fake), self.assertRaises(RuntimeError) as ctx:
                store.upsert_text(docs, vecs)
            self.assertIn("OpenMP", str(ctx.exception))
            # FLAT needs no faiss, so the same write is allowed
            flat = MilvusStore(uri=str(Path(tmp) / "f.db"), index_type="FLAT")
            with patch.dict(sys.modules, fake):
                flat.upsert_text(docs, vecs)
            self.assertEqual(flat.text_count(), 20)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAS_MILVUS, "pymilvus / milvus-lite not installed")
class LoaderSubprocessTests(unittest.TestCase):
    def test_artifact_round_trip(self) -> None:
        from rag.store.load import load_artifact, save_artifact

        tmp = tempfile.mkdtemp()
        try:
            docs, vecs = _docs(12)
            path = save_artifact(Path(tmp) / "a.npz", [d.id for d in docs], vecs, [d.metadata for d in docs],
                                 [d.text for d in docs], properties={"origin_fp": "abc"})
            ids, v, metas, texts, props = load_artifact(path)
            self.assertEqual(props, {"origin_fp": "abc"})
            self.assertEqual(ids, [d.id for d in docs])
            np.testing.assert_allclose(v, vecs, atol=1e-7)
            self.assertEqual(metas[3], docs[3].metadata)
            self.assertEqual(texts[5], "商品 5")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_server_mode_end_to_end(self) -> None:
        """milvus-lite as a separate server process: the loader writes over gRPC, this
        (possibly torch-loaded) process searches over gRPC, and faiss never enters it."""
        import socket
        import time

        from rag.store.load import save_artifact
        from rag.store.milvus_store import MilvusStore

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        tmp = tempfile.mkdtemp()
        server = subprocess.Popen(
            [sys.executable, "-m", "milvus_lite", "server", "--data-dir", str(Path(tmp) / "srv.db"),
             "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.3)
            uri = f"http://127.0.0.1:{port}"
            docs, vecs = _docs(300)
            art = save_artifact(Path(tmp) / "t.npz", [d.id for d in docs], vecs, [d.metadata for d in docs],
                                [d.text for d in docs], properties={"origin_fp": "fp-server"})
            env = {**os.environ, "RAG_STORE": "milvus", "RAG_MILVUS_URI": uri, "RAG_MILVUS_INDEX_TYPE": "HNSW"}
            proc = subprocess.run(
                [sys.executable, "-m", "rag.store.load", str(art), "--collection", "text", "--rebuild"],
                cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            self.assertIn("upserted 300", proc.stdout)

            store = MilvusStore(uri=uri, index_type="HNSW")
            self.assertFalse(store.write_needs_isolation())  # server mode is never isolated
            self.assertEqual(store.text_count(), 300)
            self.assertEqual(store.index_properties(), {"origin_fp": "fp-server"})
            self.assertEqual(store.query_text(vecs[42], 1)[0].id, docs[42].id)
            pred = lambda m: m["sub_category"] == "耳机"  # noqa: E731
            hits = store.query_text(vecs[7], 60, where={"sub_category": {"$in": ["耳机"]}})
            self.assertEqual([h.id for h in hits], _exact(vecs, docs, vecs[7], 60, pred))
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
            shutil.rmtree(tmp, ignore_errors=True)


class PushdownGatingTests(unittest.TestCase):
    JP = {"product_id": "x", "brand": "资生堂", "category": "美妆护肤", "provenance": {"origin_country": "JP"}}
    FR = {"product_id": "y", "brand": "理肤泉", "category": "美妆护肤", "provenance": {"origin_country": "FR"}}

    def _filter(self) -> Filter:
        return Filter(category="美妆护肤", exclude_keywords=["日系"])

    def test_python_side_rule_follows_the_flag(self) -> None:
        with patch.dict(os.environ, {"RAG_FILTER_PUSHDOWN": "1"}):
            self.assertFalse(product_matches_filter(self.JP, self._filter()))
            self.assertTrue(product_matches_filter(self.FR, self._filter()))
        with patch.dict(os.environ, {"RAG_FILTER_PUSHDOWN": "0"}):
            self.assertTrue(product_matches_filter(self.JP, self._filter()))

    def test_db_pushdown_only_when_index_has_the_field(self) -> None:
        from rag.retrieve.query import _current_origin_fp

        current = _current_origin_fp()

        class FakeStore:
            def __init__(self, fields, fp=current):
                self._fields = frozenset(fields)
                self._fp = fp

            def filterable_fields(self, collection=TEXT_COLLECTION):
                return self._fields

            def index_properties(self, collection=TEXT_COLLECTION):
                return {"origin_fp": self._fp} if self._fp else {}

        v2 = {"category", "brand_country"}
        with patch.dict(os.environ, {"RAG_FILTER_PUSHDOWN": "1"}):
            with patch("rag.store.get_store", return_value=FakeStore(v2)):
                self.assertIn("brand_country", fields_in_where(_build_where(self._filter())))
            with patch("rag.store.get_store", return_value=FakeStore({"category"})):  # legacy index
                self.assertNotIn("brand_country", fields_in_where(_build_where(self._filter())))
            # brand_country was computed with an older origin table -> do not trust it in the DB
            with patch("rag.store.get_store", return_value=FakeStore(v2, fp="stale")):
                self.assertNotIn("brand_country", fields_in_where(_build_where(self._filter())))
            with patch("rag.store.get_store", return_value=FakeStore(v2, fp=None)):
                self.assertNotIn("brand_country", fields_in_where(_build_where(self._filter())))
        with patch.dict(os.environ, {"RAG_FILTER_PUSHDOWN": "0"}):
            with patch("rag.store.get_store", return_value=FakeStore(v2)):
                self.assertNotIn("brand_country", fields_in_where(_build_where(self._filter())))

    def test_origin_fingerprint_tracks_the_resolution(self) -> None:
        from rag.retrieve.brand_origin import origin_fingerprint

        a = [{"product_id": "x", "brand": "资生堂"}, {"product_id": "y", "brand": "理肤泉"}]
        b = [{"product_id": "y", "brand": "理肤泉"}, {"product_id": "x", "brand": "资生堂"}]
        c = [{"product_id": "x", "brand": "理肤泉"}, {"product_id": "y", "brand": "理肤泉"}]
        self.assertEqual(origin_fingerprint(a), origin_fingerprint(b))  # order-independent
        self.assertNotEqual(origin_fingerprint(a), origin_fingerprint(c))  # x: JP -> FR


@unittest.skipUnless(HAS_MILVUS, "pymilvus / milvus-lite not installed")
class MigrationGuardTests(unittest.TestCase):
    def test_refuses_to_copy_a_pre_v2_index(self) -> None:
        import chromadb

        from rag.store import migrate as mig
        from rag.store.milvus_store import MilvusStore

        tmp = tempfile.mkdtemp()
        try:
            docs, vecs = _docs(30)
            client = chromadb.PersistentClient(path=str(Path(tmp) / "c"))
            col = client.get_or_create_collection(TEXT_COLLECTION, metadata={"hnsw:space": "cosine"})
            legacy = [{k: v for k, v in d.metadata.items() if k not in ("brand_country", "currency")} for d in docs]
            col.upsert(ids=[d.id for d in docs], embeddings=vecs.tolist(), metadatas=legacy)
            src, dst = ChromaStore(Path(tmp) / "c"), MilvusStore(uri=str(Path(tmp) / "m.db"), index_type=IN_PROCESS_INDEX)
            v2 = ChromaStore(Path(tmp) / "v2")
            v2.reset_collection(TEXT_COLLECTION, properties={"origin_fp": "fp-src"})
            v2.upsert_text(docs, vecs)
            with patch.object(mig, "get_store", side_effect=lambda name: src if name == "chroma" else dst):
                self.assertEqual(mig.migrate("chroma", "milvus", ["text"], rebuild=True, allow_legacy=False, batch=10), 3)
                self.assertEqual(mig.migrate("chroma", "milvus", ["text"], rebuild=True, allow_legacy=True, batch=10), 0)
            self.assertEqual(dst.text_count(), 30)
            with patch.object(mig, "get_store", side_effect=lambda name: v2 if name == "chroma" else dst):
                self.assertEqual(mig.migrate("chroma", "milvus", ["text"], rebuild=True, allow_legacy=False, batch=10), 0)
            self.assertEqual(dst.index_properties(), {"origin_fp": "fp-src"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
