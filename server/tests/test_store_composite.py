"""P1 — ShadowStore / FallbackStore (rag/store/composite.py) and their wiring
into rag.store.get_store(). Pure fakes: no Chroma, no Milvus, no models.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import rag.store as store_mod  # noqa: E402
from rag.store.base import Hit  # noqa: E402
from rag.store.composite import (  # noqa: E402
    FallbackStore,
    ShadowStore,
    overlap_at_k,
    parse_until,
    unwrap,
    where_hash,
    wrapper_stats,
)


class FakeStore:
    def __init__(self, backend="fake", ids=("a", "b", "c"), fail=False, delay=0.0, gate=None):
        self.backend = backend
        self.ids = list(ids)
        self.fail = fail
        self.delay = delay
        self.gate = gate  # threading.Event: block until set
        self.calls = []
        self.writes = []

    def _hits(self, k):
        if self.gate is not None:
            self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.backend} down")
        return [Hit(id=i, score=1.0 - n * 0.1, metadata={"product_id": i}) for n, i in enumerate(self.ids[:k])]

    def query_text(self, embedding, k=5, *, where=None):
        self.calls.append(("text", k, where))
        return self._hits(k)

    def query_image(self, embedding, k=5):
        self.calls.append(("image", k, None))
        return self._hits(k)

    def upsert_text(self, docs, embeddings):
        self.writes.append(("upsert_text", len(docs)))

    def seal(self, name):
        self.writes.append(("seal", name))

    def text_count(self):
        return len(self.ids)

    def schema_info(self):
        return {"backend": self.backend}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class HelperTests(unittest.TestCase):
    def test_overlap_and_where_hash(self) -> None:
        self.assertEqual(overlap_at_k(["a", "b"], ["b", "a"], 2), 1.0)
        self.assertEqual(overlap_at_k(["a", "b"], ["a", "c"], 2), 0.5)
        self.assertEqual(overlap_at_k([], [], 5), 1.0)
        self.assertEqual(overlap_at_k(["a"], [], 5), 0.0)
        self.assertEqual(where_hash(None), "")
        # key order must not change the hash
        self.assertEqual(where_hash({"a": 1, "b": {"$in": [1, 2]}}), where_hash({"b": {"$in": [1, 2]}, "a": 1}))
        self.assertNotEqual(where_hash({"a": 1}), where_hash({"a": 2}))

    def test_parse_until(self) -> None:
        self.assertEqual(parse_until("2026-10-20"), dt.date(2026, 10, 20))
        self.assertIsNone(parse_until(""))
        self.assertIsNone(parse_until("20/10/2026"))


class ShadowStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.log = Path(self.tmp) / "shadow" / "s.jsonl"

    def _make(self, primary, shadow, **kw):
        return ShadowStore(primary, lambda: shadow, shadow_name=shadow.backend, log_path=self.log, **kw)

    def test_primary_answers_and_shadow_line_is_logged(self) -> None:
        primary = FakeStore("chroma", ids=("a", "b", "c"))
        shadow = FakeStore("milvus", ids=("a", "c", "d"))
        st = self._make(primary, shadow, timeout_ms=5000)
        hits = st.query_text([0.1, 0.2], 3, where={"category": "耳机"})
        self.assertEqual([h.id for h in hits], ["a", "b", "c"])  # primary's answer, untouched
        self.assertTrue(st.drain(5))
        rows = _read_jsonl(self.log)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        for key in ("ts", "collection", "k", "where_hash", "primary_ids", "shadow_ids", "overlap_at_k",
                    "primary_ms", "shadow_ms", "error"):
            self.assertIn(key, r)
        self.assertEqual(r["collection"], "products_text")
        self.assertEqual(r["k"], 3)
        self.assertEqual(r["where_hash"], where_hash({"category": "耳机"}))
        self.assertEqual(r["primary_ids"], ["a", "b", "c"])
        self.assertEqual(r["shadow_ids"], ["a", "c", "d"])
        self.assertAlmostEqual(r["overlap_at_k"], 2 / 3, places=3)
        self.assertIsNone(r["error"])
        self.assertEqual((r["primary"], r["shadow"]), ("chroma", "milvus"))
        self.assertEqual(shadow.calls, [("text", 3, {"category": "耳机"})])

    def test_image_queries_are_shadowed_too(self) -> None:
        st = self._make(FakeStore("chroma"), FakeStore("milvus"), timeout_ms=5000)
        st.query_image([0.3], 2)
        self.assertTrue(st.drain(5))
        self.assertEqual(_read_jsonl(self.log)[0]["collection"], "products_image")

    def test_shadow_failure_never_reaches_the_primary_path(self) -> None:
        st = self._make(FakeStore("chroma"), FakeStore("milvus", fail=True), timeout_ms=5000)
        hits = st.query_text([0.1], 2)
        self.assertEqual(len(hits), 2)
        self.assertTrue(st.drain(5))
        r = _read_jsonl(self.log)[0]
        self.assertIn("milvus down", r["error"])
        self.assertIsNone(r["overlap_at_k"])
        self.assertEqual(st.stats["errors"], 1)

    def test_shadow_factory_failure_is_contained(self) -> None:
        def boom():
            raise ConnectionError("no route to milvus")

        st = ShadowStore(FakeStore("chroma"), boom, shadow_name="milvus", log_path=self.log)
        self.assertEqual(len(st.query_text([0.1], 2)), 2)
        self.assertTrue(st.drain(5))
        self.assertIn("no route", _read_jsonl(self.log)[0]["error"])

    def test_slow_shadow_does_not_slow_primary_and_is_marked_timeout(self) -> None:
        st = self._make(FakeStore("chroma"), FakeStore("milvus", delay=0.3), timeout_ms=50)
        t0 = time.perf_counter()
        st.query_text([0.1], 2)
        self.assertLess(time.perf_counter() - t0, 0.15)  # primary returns before the shadow finishes
        self.assertTrue(st.drain(5))
        r = _read_jsonl(self.log)[0]
        self.assertTrue(r["error"].startswith("timeout"))
        self.assertEqual(st.stats["timeouts"], 1)

    def test_bounded_queue_drops_instead_of_blocking(self) -> None:
        release = threading.Event()
        shadow = FakeStore("milvus", gate=release)
        st = self._make(FakeStore("chroma"), shadow, timeout_ms=60_000, workers=1, queue_max=2)
        t0 = time.perf_counter()
        for _ in range(10):
            st.query_text([0.1], 1)
        self.assertLess(time.perf_counter() - t0, 0.5)  # never blocked on the stuck shadow
        self.assertEqual(st.stats["submitted"], 2)
        self.assertEqual(st.stats["dropped"], 8)
        release.set()
        self.assertTrue(st.drain(5))
        self.assertEqual(len(_read_jsonl(self.log)), 2)

    def test_stuck_shadow_never_blocks_interpreter_exit(self) -> None:
        # ThreadPoolExecutor 的线程在解释器退出时会被 join、排队任务会跑完:影子库不应答时
        # systemctl restart 会被拖住。影子线程必须是 daemon。
        release = threading.Event()
        st = self._make(FakeStore("chroma"), FakeStore("milvus", gate=release), workers=2, queue_max=8)
        for _ in range(4):
            st.query_text([0.1], 1)
        shadow_threads = [t for t in threading.enumerate() if t.name.startswith("rag-shadow")]
        self.assertTrue(shadow_threads)
        self.assertTrue(all(t.daemon for t in shadow_threads))
        self.assertLessEqual(len(st._workers), 2)
        release.set()
        self.assertTrue(st.drain(5))

    def test_primary_error_propagates_and_is_logged(self) -> None:
        st = self._make(FakeStore("milvus", fail=True), FakeStore("chroma"), timeout_ms=5000)
        with self.assertRaises(RuntimeError):
            st.query_text([0.1], 2)
        self.assertTrue(st.drain(5))
        r = _read_jsonl(self.log)[0]
        self.assertIsNone(r["primary_ids"])
        self.assertIn("milvus down", r["primary_error"])
        self.assertEqual(r["shadow_ids"], ["a", "b"])

    def test_writes_and_other_methods_go_to_primary_only(self) -> None:
        primary, shadow = FakeStore("chroma"), FakeStore("milvus")
        st = self._make(primary, shadow)
        st.upsert_text([1, 2], [[0.1], [0.2]])
        self.assertTrue(hasattr(st, "seal"))
        st.seal("products_text")
        self.assertEqual(primary.writes, [("upsert_text", 2), ("seal", "products_text")])
        self.assertEqual(shadow.writes, [])
        self.assertEqual(st.backend, "chroma")
        self.assertEqual(st.text_count(), 3)
        self.assertFalse(hasattr(st, "write_needs_isolation"))  # absent on primary → absent here
        self.assertIs(unwrap(st), primary)
        self.assertIn("shadow", st.schema_info())

    def test_summary_groups_by_collection_and_filter(self) -> None:
        from rag.store.composite import summarize_shadow_log

        st = self._make(FakeStore("chroma", ids=("a", "b")), FakeStore("milvus", ids=("a", "c")), timeout_ms=5000)
        st.query_text([0.1], 2)
        st.query_text([0.1], 2, where={"brand": "x"})
        st.query_image([0.1], 2)
        self.assertTrue(st.drain(5))
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write("not json\n")
        rep = summarize_shadow_log(self.log)
        self.assertEqual(rep["bad_lines"], 1)
        self.assertEqual(set(rep["groups"]), {"products_text|unfiltered", "products_text|filtered",
                                              "products_image|unfiltered"})
        g = rep["groups"]["products_text|filtered"]
        self.assertEqual((g["lines"], g["overlap_mean"], g["overlap_lt_1"]), (1, 0.5, 1.0))

    def test_log_write_failure_is_swallowed(self) -> None:
        bad = Path(self.tmp) / "file"
        bad.write_text("x")
        st = ShadowStore(FakeStore("chroma"), lambda: FakeStore("milvus"), shadow_name="milvus",
                         log_path=bad / "nested" / "s.jsonl")  # parent is a file → mkdir fails
        self.assertEqual(len(st.query_text([0.1], 1)), 1)
        self.assertTrue(st.drain(5))
        self.assertEqual(st.stats["log_errors"], 1)


class FallbackStoreTests(unittest.TestCase):
    def _make(self, primary, fallback, until, today, **kw):
        kw.setdefault("cooldown_s", 0)  # 这些用例逐次检查主存储;熔断单独测
        return FallbackStore(primary, lambda: fallback, fallback_name=fallback.backend, until=until,
                             today=lambda: today, **kw)

    def test_healthy_primary_never_touches_the_fallback(self) -> None:
        fb = FakeStore("chroma")
        st = self._make(FakeStore("milvus"), fb, dt.date(2026, 10, 20), dt.date(2026, 10, 6))
        self.assertEqual(len(st.query_text([0.1], 2)), 2)
        self.assertEqual(fb.calls, [])
        self.assertEqual(st.stats["primary_errors"], 0)

    def test_primary_error_is_counted_and_fallback_answers(self) -> None:
        fb = FakeStore("chroma", ids=("x", "y"))
        st = self._make(FakeStore("milvus", fail=True), fb, dt.date(2026, 10, 20), dt.date(2026, 10, 6))
        hits = st.query_text([0.1], 2, where={"brand": "A"})
        self.assertEqual([h.id for h in hits], ["x", "y"])
        self.assertEqual(fb.calls, [("text", 2, {"brand": "A"})])
        self.assertEqual(st.query_image([0.1], 1)[0].id, "x")
        self.assertEqual(st.stats["primary_errors"], 2)
        self.assertEqual(st.stats["served_by_fallback"], 2)
        self.assertIn("milvus down", st.describe()["last_error"])

    def test_until_date_is_inclusive_and_expiry_disables_with_warning(self) -> None:
        primary = FakeStore("milvus", fail=True)
        on_day = self._make(primary, FakeStore("chroma"), dt.date(2026, 10, 20), dt.date(2026, 10, 20))
        self.assertTrue(on_day.active())
        self.assertEqual(len(on_day.query_text([0.1], 1)), 1)

        expired = self._make(primary, FakeStore("chroma"), dt.date(2026, 10, 20), dt.date(2026, 10, 21))
        self.assertFalse(expired.active())
        with patch("sys.stderr") as err:
            with self.assertRaises(RuntimeError):
                expired.query_text([0.1], 1)
            with self.assertRaises(RuntimeError):
                expired.query_text([0.1], 1)
        warnings = [c for c in err.write.call_args_list if "expired" in str(c)]
        self.assertEqual(len(warnings), 1)  # warned once, not on every request
        self.assertEqual(expired.stats["expired_skips"], 2)

    def test_fallback_failure_reraises_the_primary_error(self) -> None:
        st = self._make(FakeStore("milvus", fail=True), FakeStore("chroma", fail=True),
                        dt.date(2026, 10, 20), dt.date(2026, 10, 6))
        with self.assertRaisesRegex(RuntimeError, "milvus down"):
            st.query_text([0.1], 1)
        self.assertEqual(st.stats["fallback_errors"], 1)


    def test_cooldown_skips_a_failing_primary_then_retries_it(self) -> None:
        now = [100.0]
        primary = FakeStore("milvus", fail=True)
        fb = FakeStore("chroma", ids=("x",))
        st = self._make(primary, fb, dt.date(2026, 10, 20), dt.date(2026, 10, 6),
                        cooldown_s=10, clock=lambda: now[0])
        with patch("sys.stderr"):
            for _ in range(5):
                self.assertEqual(st.query_text([0.1], 1)[0].id, "x")
        # 只有第一次真的去试了主存储(不应答的主存储每次都要等满超时)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(st.stats["primary_errors"], 1)
        self.assertEqual(st.stats["primary_skipped_cooldown"], 4)
        self.assertEqual(st.stats["served_by_fallback"], 5)
        self.assertTrue(st.describe()["primary_skipped_now"])
        # 冷却期过后再试主存储;恢复了就回到主存储
        now[0] += 11
        primary.fail = False
        self.assertEqual(st.query_text([0.1], 1)[0].id, "a")
        self.assertEqual(len(primary.calls), 2)
        self.assertFalse(st.describe()["primary_skipped_now"])

    def test_cooldown_does_not_apply_after_expiry(self) -> None:
        now = [100.0]
        primary = FakeStore("milvus", fail=True)
        st = self._make(primary, FakeStore("chroma"), dt.date(2026, 10, 20), dt.date(2026, 10, 21),
                        cooldown_s=10, clock=lambda: now[0])
        with patch("sys.stderr"):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    st.query_text([0.1], 1)
        self.assertEqual(len(primary.calls), 2)

    def test_fallback_failure_during_cooldown_still_tries_primary(self) -> None:
        now = [100.0]
        primary = FakeStore("milvus", fail=True)
        fb = FakeStore("chroma", fail=True)
        st = self._make(primary, fb, dt.date(2026, 10, 20), dt.date(2026, 10, 6),
                        cooldown_s=10, clock=lambda: now[0])
        with patch("sys.stderr"):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "milvus down"):
                    st.query_text([0.1], 1)
        self.assertEqual(len(primary.calls), 2)


class GetStoreWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stores = dict(store_mod._stores)
        self._wrapped = dict(store_mod._wrapped)
        store_mod._wrapped.clear()
        self.fakes = {"chroma": FakeStore("chroma"), "milvus": FakeStore("milvus")}
        self._patch = patch.object(store_mod, "_raw_store", side_effect=lambda name: self.fakes[name.strip().lower()])
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        store_mod._wrapped.clear()
        store_mod._wrapped.update(self._wrapped)
        store_mod._stores.clear()
        store_mod._stores.update(self._stores)

    def _env(self, **kw):
        base = {"RAG_STORE": "", "RAG_STORE_SHADOW": "", "RAG_STORE_FALLBACK": "", "RAG_STORE_FALLBACK_UNTIL": ""}
        base.update(kw)
        return patch.dict(os.environ, base)

    def test_default_is_the_bare_store(self) -> None:
        with self._env():
            self.assertIs(store_mod.get_store(), self.fakes["chroma"])
            self.assertEqual(store_mod.store_wrapper_stats(), {})

    def test_explicit_backend_is_never_wrapped(self) -> None:
        with self._env(RAG_STORE_SHADOW="milvus"):
            self.assertIs(store_mod.get_store("milvus"), self.fakes["milvus"])

    def test_shadow_and_fallback_wrap_in_the_right_order(self) -> None:
        future = (dt.date.today() + dt.timedelta(days=7)).isoformat()
        with self._env(RAG_STORE="milvus", RAG_STORE_SHADOW="chroma", RAG_STORE_FALLBACK="chroma",
                       RAG_STORE_FALLBACK_UNTIL=future):
            st = store_mod.get_store()
            self.assertIsInstance(st, FallbackStore)
            self.assertIsInstance(st.primary, ShadowStore)
            self.assertIs(unwrap(st), self.fakes["milvus"])
            self.assertIs(store_mod.get_store(), st)  # cached
            self.assertEqual(set(wrapper_stats(st)), {"shadow", "fallback"})
            self.assertEqual(st.backend, "milvus")

    def test_misconfigurations_disable_the_wrapper(self) -> None:
        with patch("sys.stderr"):
            with self._env(RAG_STORE="milvus", RAG_STORE_FALLBACK="chroma"):  # no UNTIL → refused
                self.assertIs(store_mod.get_store(), self.fakes["milvus"])
            store_mod._wrapped.clear()
            with self._env(RAG_STORE="milvus", RAG_STORE_FALLBACK="chroma", RAG_STORE_FALLBACK_UNTIL="next week"):
                self.assertIs(store_mod.get_store(), self.fakes["milvus"])
            store_mod._wrapped.clear()
            with self._env(RAG_STORE="chroma", RAG_STORE_SHADOW="chroma"):  # shadow == primary
                self.assertIs(store_mod.get_store(), self.fakes["chroma"])
            store_mod._wrapped.clear()
            with self._env(RAG_STORE="chroma", RAG_STORE_SHADOW="qdrant"):
                self.assertIs(store_mod.get_store(), self.fakes["chroma"])

    def test_expired_fallback_is_built_but_inactive(self) -> None:
        past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        with patch("sys.stderr"), self._env(RAG_STORE="milvus", RAG_STORE_FALLBACK="chroma",
                                            RAG_STORE_FALLBACK_UNTIL=past):
            st = store_mod.get_store()
            self.assertIsInstance(st, FallbackStore)
            self.assertFalse(st.active())

    def test_module_level_api_goes_through_the_wrapper(self) -> None:
        future = (dt.date.today() + dt.timedelta(days=3)).isoformat()
        self.fakes["milvus"].fail = True
        with patch("sys.stderr"), self._env(RAG_STORE="milvus", RAG_STORE_FALLBACK="chroma",
                                            RAG_STORE_FALLBACK_UNTIL=future):
            hits = store_mod.query_text([0.1], k=2)
        self.assertEqual(len(hits), 2)
        self.assertEqual(self.fakes["chroma"].calls, [("text", 2, None)])


class ChromaDirTests(unittest.TestCase):
    def test_default_unchanged_and_override_resolves_against_repo_root(self) -> None:
        from rag.store.chroma_store import CHROMA_DIR, REPO_ROOT as STORE_ROOT, ChromaStore, chroma_dir

        with patch.dict(os.environ, {"RAG_CHROMA_DIR": ""}):
            self.assertEqual(chroma_dir(), CHROMA_DIR)
            self.assertEqual(ChromaStore()._path, CHROMA_DIR)
        with patch.dict(os.environ, {"RAG_CHROMA_DIR": "data/.chroma_v2"}):
            self.assertEqual(chroma_dir(), STORE_ROOT / "data" / ".chroma_v2")
            self.assertEqual(ChromaStore()._path, STORE_ROOT / "data" / ".chroma_v2")
        with patch.dict(os.environ, {"RAG_CHROMA_DIR": "/srv/x"}):
            self.assertEqual(chroma_dir(), Path("/srv/x"))


if __name__ == "__main__":
    unittest.main()
