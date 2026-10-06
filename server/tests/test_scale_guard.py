"""P1 — rag.bench.scale --milvus-uri 的"绝不 reset 生产库"守卫。

接外部 Milvus 之后,压测的 ingest() 会 drop 目标库里的 products_text;指错地方就是删生产索引。
这里只测守卫本身(假存储,不连任何服务)。
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rag.bench import scale  # noqa: E402


class FakeStore:
    def __init__(self, *, exists=False, alias=None, props=None):
        self.exists, self.alias, self.props = exists, alias, props or {}

    def alias_target(self, name):
        return self.alias

    def _exists(self, name):
        return self.exists or self.alias is not None

    def index_properties(self, name):
        return dict(self.props)


URI = "http://10.0.0.5:19530"


class ScaleGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"RAG_MILVUS_URI": "", "RAG_MILVUS_DB": ""})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def test_default_database_is_refused(self) -> None:
        for db in ("", "default", "DEFAULT"):
            with self.assertRaisesRegex(RuntimeError, "default"):
                scale.check_external_target(FakeStore(), URI, db)

    def test_alias_or_unmarked_collection_is_refused(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "alias"):
            scale.check_external_target(FakeStore(alias="products_text__v2_202610061830"), URI, "lionpick_bench")
        with self.assertRaisesRegex(RuntimeError, "not created by"):
            scale.check_external_target(FakeStore(exists=True, props={"origin_fp": "abc"}), URI, "lionpick_bench")

    def test_this_environments_production_target_is_refused(self) -> None:
        with patch.dict(os.environ, {"RAG_MILVUS_URI": URI + "/", "RAG_MILVUS_DB": "lionpick_bench"}):
            with self.assertRaisesRegex(RuntimeError, "this environment"):
                scale.check_external_target(FakeStore(), URI, "lionpick_bench")

    def test_empty_db_or_own_previous_run_is_allowed(self) -> None:
        scale.check_external_target(FakeStore(), URI, "lionpick_bench")
        scale.check_external_target(
            FakeStore(exists=True, props={scale.BENCH_MARK[0]: scale.BENCH_MARK[1], "n": "1000"}), URI, "lionpick_bench"
        )

    def test_cli_refuses_default_db_before_connecting(self) -> None:
        args = type("A", (), {"milvus_uri": URI, "milvus_db": "default", "keep": False})()
        with patch.object(scale, "_ensure_bench_db", side_effect=AssertionError("must not connect")), \
                redirect_stderr(io.StringIO()) as err:
            rc = scale._run_external_milvus(args, {"backends": {}}, [], None, [], [], None, None, [], {}, [])
        self.assertEqual(rc, 2)
        self.assertIn("default", err.getvalue())

    def test_reuse_and_calibrate_are_rejected_with_milvus_uri(self) -> None:
        for flag in ("--reuse", "--calibrate"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                scale.main(["--milvus-uri", URI, flag])


if __name__ == "__main__":
    unittest.main()
