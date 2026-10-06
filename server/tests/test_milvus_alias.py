"""P1 — versioned Milvus collections + alias switching (RAG_MILVUS_VERSIONED=1).

Two layers:

* ``FakeMilvusClient`` mimics the server-side alias semantics of Milvus
  Standalone that matter here (has_collection resolves aliases, dropping or
  creating "through" an alias is refused) so the naming / retention / rollback
  logic is pinned without any Milvus at all;
* ``LiteServerAliasTests`` runs the same flow against the real milvus-lite
  3.2.1 implementation (it does support create/alter/describe/list alias and
  rename_collection) as a separate server process, through the real
  ``rag.store.load`` ingest path.

Not covered here: real Milvus Standalone 3.0.2 (no Docker in this test run).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from rag.store.base import IMAGE_COLLECTION, TEXT_COLLECTION, Doc  # noqa: E402
from rag.store.milvus_store import (  # noqa: E402
    MilvusStore,
    is_physical_name,
    logical_name,
    new_physical_name,
    physical_sort_key,
)

HAS_MILVUS = importlib.util.find_spec("pymilvus") is not None and importlib.util.find_spec("milvus_lite") is not None


def _docs(n: int, tag: str, dim: int = 8, seed: int = 3):
    rng = np.random.default_rng(seed)
    vecs = rng.normal(size=(n, dim)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    docs = [
        Doc(
            id=f"{tag}-{i}",
            text=f"{tag} 商品 {i}",
            metadata={
                "product_id": f"p{i}", "chunk_type": "desc", "category": "美妆护肤", "sub_category": "面霜",
                "brand": f"品牌{i % 3}", "brand_country": "CN", "currency": "CNY", "base_price": 10.0 + i,
            },
        )
        for i in range(n)
    ]
    return docs, vecs.tolist()


class NamingTests(unittest.TestCase):
    def test_physical_names_round_trip(self) -> None:
        now = dt.datetime(2026, 10, 6, 18, 30)
        name = new_physical_name(TEXT_COLLECTION, set(), now=now)
        self.assertEqual(name, "products_text__v2_202610061830")
        self.assertEqual(logical_name(name), TEXT_COLLECTION)
        self.assertTrue(is_physical_name(name))
        self.assertFalse(is_physical_name(TEXT_COLLECTION))
        self.assertEqual(logical_name(TEXT_COLLECTION), TEXT_COLLECTION)
        # same minute → sequence suffix, never a collision
        second = new_physical_name(TEXT_COLLECTION, {name}, now=now)
        self.assertEqual(second, "products_text__v2_202610061830_2")
        self.assertEqual(logical_name("products_image__v2_202610061830_7"), IMAGE_COLLECTION)

    def test_sort_order_puts_legacy_first_within_a_minute(self) -> None:
        names = [
            "products_text__v2_202610061830_2",
            "products_text__v2_202610061830",
            "products_text__legacy_202610061830",
            "products_text__v2_202610051200",
        ]
        self.assertEqual(sorted(names, key=physical_sort_key), [
            "products_text__v2_202610051200",
            "products_text__legacy_202610061830",
            "products_text__v2_202610061830",
            "products_text__v2_202610061830_2",
        ])


class FakeMilvusClient:
    """Just enough of MilvusClient, with Standalone's alias rules."""

    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}  # name -> {"rows": {id: row}, "loaded": bool, "props": {}}
        self.aliases: dict[str, str] = {}
        self.calls: list[tuple] = []

    def _resolve(self, name):
        return self.aliases.get(name, name)

    def has_collection(self, name):
        return self._resolve(name) in self.collections

    def list_collections(self):
        return list(self.collections)

    def create_collection(self, name, schema=None, index_params=None, consistency_level=None):
        self.calls.append(("create", name, consistency_level))
        if name in self.aliases:
            raise RuntimeError(f"collection name {name} conflicts with an existing alias")
        if name in self.collections:
            raise RuntimeError("exists")
        self.collections[name] = {"rows": {}, "loaded": True, "props": {}}

    def drop_collection(self, name):
        self.calls.append(("drop", name))
        if name in self.aliases:
            raise RuntimeError("cannot drop the collection via alias")
        self.collections.pop(name, None)
        for a in [a for a, t in self.aliases.items() if t == name]:
            del self.aliases[a]

    def rename_collection(self, old, new):
        self.calls.append(("rename", old, new))
        self.collections[new] = self.collections.pop(old)

    def create_alias(self, collection_name, alias):
        self.calls.append(("create_alias", collection_name, alias))
        if alias in self.collections or alias in self.aliases:
            raise RuntimeError("alias conflict")
        self.aliases[alias] = collection_name

    def alter_alias(self, collection_name, alias):
        self.calls.append(("alter_alias", collection_name, alias))
        if alias not in self.aliases:
            raise RuntimeError("no such alias")
        self.aliases[alias] = collection_name

    def list_aliases(self, collection_name=""):
        return {"aliases": sorted(self.aliases)}

    def describe_alias(self, alias):
        if alias not in self.aliases:
            raise RuntimeError(f"alias {alias} does not exist")
        return {"alias": alias, "collection_name": self.aliases[alias]}

    def prepare_index_params(self):
        class _P:
            def add_index(self, **kw):
                pass

        return _P()

    def alter_collection_properties(self, name, properties):
        self.collections[self._resolve(name)]["props"].update(properties)

    def upsert(self, name, data):
        col = self.collections[self._resolve(name)]
        for row in data:
            col["rows"][row["id"]] = row

    def flush(self, name):
        self.calls.append(("flush", name))

    def load_collection(self, name):
        self.collections[self._resolve(name)]["loaded"] = True

    def release_collection(self, name):
        self.calls.append(("release", name))
        self.collections[self._resolve(name)]["loaded"] = False

    def query(self, name, filter="", output_fields=None, limit=None):
        col = self.collections[self._resolve(name)]
        if output_fields == ["count(*)"]:
            return [{"count(*)": len(col["rows"])}]
        return list(col["rows"].values())

    def get_collection_stats(self, name):
        return {"row_count": len(self.collections[self._resolve(name)]["rows"])}

    def describe_collection(self, name):
        from rag.store.base import IMAGE_SCALAR_FIELDS, TEXT_SCALAR_FIELDS

        declared = TEXT_SCALAR_FIELDS if logical_name(self._resolve(name)) == TEXT_COLLECTION else IMAGE_SCALAR_FIELDS
        return {"fields": [{"name": "id"}, {"name": "vector"}] + [{"name": f} for f in declared],
                "properties": dict(self.collections[self._resolve(name)]["props"])}


@unittest.skipUnless(HAS_MILVUS, "pymilvus not installed (MilvusStore._create builds a pymilvus schema)")
class FakeClientAliasTests(unittest.TestCase):
    def _store(self, client, versioned):
        st = MilvusStore(uri="http://fake:19530", versioned=versioned)
        st._client_obj = client
        return st

    def _rebuild(self, st, n, tag):
        docs, vecs = _docs(n, tag)
        with redirect_stderr(io.StringIO()):
            st.reset_collection(TEXT_COLLECTION, properties={"origin_fp": "fp"})
            st.upsert_text(docs, vecs)
            st.seal(TEXT_COLLECTION)

    def test_first_versioned_build_adopts_the_legacy_collection_then_retains_two(self) -> None:
        client = FakeMilvusClient()
        plain = self._store(client, versioned=False)
        docs, vecs = _docs(5, "legacy")
        plain.upsert_text(docs, vecs)
        self.assertIn(TEXT_COLLECTION, client.collections)  # R15-style unversioned collection

        st = self._store(client, versioned=True)
        self._rebuild(st, 7, "a")
        target1 = client.aliases[TEXT_COLLECTION]
        self.assertTrue(target1.startswith("products_text__v2_"))
        self.assertEqual(len(client.collections[target1]["rows"]), 7)
        legacy = [c for c in client.collections if c.startswith("products_text__legacy_")]
        self.assertEqual(len(legacy), 1)  # renamed, kept for rollback — not dropped
        self.assertNotIn(TEXT_COLLECTION, client.collections)
        self.assertEqual(client.collections[target1]["props"].get("lionpick.origin_fp"), "fp")

        self._rebuild(st, 8, "b")
        target2 = client.aliases[TEXT_COLLECTION]
        self.assertNotEqual(target2, target1)
        self._rebuild(st, 9, "c")
        target3 = client.aliases[TEXT_COLLECTION]
        physical = st.physical_collections(TEXT_COLLECTION)
        self.assertEqual(physical, [target2, target3])  # retain 2: legacy + oldest v2 pruned
        self.assertEqual(st.text_count(), 9)  # reads through the logical name
        # the retained non-serving collection is released to save memory
        self.assertIn(("release", target2), client.calls)

    def test_never_drops_or_creates_through_an_alias(self) -> None:
        client = FakeMilvusClient()
        st = self._store(client, versioned=True)
        self._rebuild(st, 4, "a")
        plain = self._store(client, versioned=False)
        with self.assertRaisesRegex(RuntimeError, "is an alias"):
            plain.reset_collection(TEXT_COLLECTION)
        self.assertNotIn(("drop", TEXT_COLLECTION), client.calls)
        with self.assertRaisesRegex(RuntimeError, "alias with that name"):
            plain._create(TEXT_COLLECTION, 8)
        self.assertFalse([c for c in client.calls if c[0] == "create" and c[1] == TEXT_COLLECTION])
        # in-place upsert in non-versioned mode writes through the alias, no new collection
        docs, vecs = _docs(1, "extra")
        plain.upsert_text(docs, vecs)
        self.assertEqual(st.text_count(), 5)

    def test_count_mismatch_keeps_the_old_alias_and_drops_the_half_build(self) -> None:
        client = FakeMilvusClient()
        st = self._store(client, versioned=True)
        self._rebuild(st, 4, "a")
        good = client.aliases[TEXT_COLLECTION]
        docs, vecs = _docs(3, "bad")
        with redirect_stderr(io.StringIO()):
            st.reset_collection(TEXT_COLLECTION)
            st.upsert_text(docs, vecs)
            staged = st._staging[TEXT_COLLECTION]["physical"]
            st._staging[TEXT_COLLECTION]["ids"].add("never-written")
            with self.assertRaisesRegex(RuntimeError, "NOT switched"):
                st.seal(TEXT_COLLECTION)
        self.assertEqual(client.aliases[TEXT_COLLECTION], good)
        self.assertNotIn(staged, client.collections)

    def test_rollback_and_switch(self) -> None:
        client = FakeMilvusClient()
        st = self._store(client, versioned=True)
        self._rebuild(st, 4, "a")
        first = client.aliases[TEXT_COLLECTION]
        self._rebuild(st, 6, "b")
        second = client.aliases[TEXT_COLLECTION]
        with redirect_stderr(io.StringIO()):
            old, new = st.rollback_alias(TEXT_COLLECTION)
        self.assertEqual((old, new), (second, first))
        self.assertEqual(st.text_count(), 4)
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "no physical collection older"):
                st.rollback_alias(TEXT_COLLECTION)
            st.activate(second)
        self.assertEqual(client.aliases[TEXT_COLLECTION], second)
        with self.assertRaises(ValueError):
            st.activate(TEXT_COLLECTION)  # a logical name is not a switch target
        with self.assertRaises(ValueError):
            st.switch_alias(TEXT_COLLECTION, "products_image__v2_202610061830")

    def test_refuses_to_switch_to_an_empty_collection(self) -> None:
        client = FakeMilvusClient()
        st = self._store(client, versioned=True)
        self._rebuild(st, 4, "a")
        client.collections["products_text__v2_209901010000"] = {"rows": {}, "loaded": False, "props": {}}
        with self.assertRaisesRegex(RuntimeError, "empty"):
            st.activate("products_text__v2_209901010000")

    def test_alias_cli(self) -> None:
        from rag.store import alias as cli

        client = FakeMilvusClient()
        st = self._store(client, versioned=True)
        self._rebuild(st, 4, "a")
        self._rebuild(st, 6, "b")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--list"], store=st), 0)
        self.assertIn("products_text  ->  products_text__v2_", out.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--rollback", "--collection", "text"], store=st), 0)
            # image has no alias → rollback fails with a non-zero exit, text untouched
            self.assertEqual(cli.main(["--rollback", "--collection", "image"], store=st), 1)
            self.assertEqual(cli.main(["--switch", "products_text__v2_000000000000"], store=st), 1)
        self.assertEqual(st.text_count(), 4)

    def test_default_mode_is_unversioned_and_unchanged(self) -> None:
        client = FakeMilvusClient()
        st = self._store(client, versioned=None)  # reads RAG_MILVUS_VERSIONED (unset → 0)
        docs, vecs = _docs(3, "a")
        st.reset_collection(TEXT_COLLECTION)
        st.upsert_text(docs, vecs)
        st.seal(TEXT_COLLECTION)
        self.assertEqual(list(client.collections), [TEXT_COLLECTION])
        self.assertEqual(client.aliases, {})
        self.assertEqual(("create", TEXT_COLLECTION, "Strong"), [c for c in client.calls if c[0] == "create"][0])


@unittest.skipUnless(HAS_MILVUS, "pymilvus / milvus-lite not installed")
class LiteServerAliasTests(unittest.TestCase):
    """Same flow against the real milvus-lite 3.2.1 implementation, in **server mode**:
    faiss lives in the child server process, so this pytest process (which other
    tests fill with torch) never loads it — see MilvusStore.write_needs_isolation.
    The ingest path is exercised end to end through `python -m rag.store.load`."""

    @classmethod
    def setUpClass(cls) -> None:
        import socket
        import subprocess
        import time

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.tmp = tempfile.mkdtemp()
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "milvus_lite", "server", "--data-dir", str(Path(cls.tmp) / "srv.db"),
             "--host", "127.0.0.1", "--port", str(cls.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", cls.port)) == 0:
                    break
            time.sleep(0.3)
        cls.uri = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        import subprocess

        cls.server.terminate()
        try:
            cls.server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            cls.server.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _load(self, n: int, tag: str, versioned: str = "1"):
        import os
        import subprocess

        from rag.store.load import save_artifact

        docs, vecs = _docs(n, tag, seed=n)
        art = save_artifact(Path(self.tmp) / f"{tag}.npz", [d.id for d in docs], vecs, [d.metadata for d in docs],
                            [d.text for d in docs], properties={"origin_fp": tag})
        env = {**os.environ, "RAG_STORE": "milvus", "RAG_MILVUS_URI": self.uri, "RAG_MILVUS_INDEX_TYPE": "HNSW",
               "RAG_MILVUS_VERSIONED": versioned}
        proc = subprocess.run(
            [sys.executable, "-m", "rag.store.load", str(art), "--collection", "text", "--rebuild"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300,
        )
        return proc, docs, vecs

    def test_versioned_ingest_switch_rollback_over_grpc(self) -> None:
        # 1) an R15-style unversioned index already exists
        proc, _, _ = self._load(6, "legacy", versioned="0")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        reader = MilvusStore(uri=self.uri)  # a "serving process": only knows the logical name
        self.assertIsNone(reader.alias_target(TEXT_COLLECTION))
        self.assertEqual(reader.text_count(), 6)

        # 2) two versioned rebuilds through the real ingest loader
        for n, tag in ((10, "a"), (12, "b")):
            proc, docs, vecs = self._load(n, tag)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            self.assertIn("serving collection: products_text__v2_", proc.stdout)
        target = reader.alias_target(TEXT_COLLECTION)
        self.assertTrue(target and target.startswith("products_text__v2_"))
        # the long-lived reader sees the switch without reconnecting
        self.assertEqual(reader.text_count(), 12)
        self.assertEqual(reader.query_text(vecs[5], 1)[0].id, docs[5].id)
        self.assertEqual(reader.index_properties(), {"origin_fp": "b"})
        self.assertIn("brand_country", reader.filterable_fields())
        info = reader.schema_info()["text"]
        self.assertEqual((info["count"], info["schema_version"], info["physical"]), (12, 2, target))
        # retain 2: the renamed legacy collection was pruned by the second build
        self.assertEqual(len(reader.physical_collections(TEXT_COLLECTION)), 2)

        # 3) rollback via the CLI, then a non-versioned rebuild must refuse
        from rag.store import alias as cli

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--rollback", "--collection", "text", "--uri", self.uri]), 0)
        self.assertEqual(reader.text_count(), 10)
        proc, _, _ = self._load(3, "oops", versioned="0")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("is an alias", proc.stderr)
        self.assertEqual(reader.text_count(), 10)  # Lite would have dropped the alias target here


if __name__ == "__main__":
    unittest.main()
