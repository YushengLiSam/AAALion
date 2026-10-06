"""Milvus 读 RPC 超时 + 合格条数缓存不信 0(P1 审查补)。

背景:pymilvus 3.0.2 对 UNAVAILABLE 默认重试 75 次且不带 deadline;服务"端口在、不应答"
时(实测 milvus-lite 服务进程 SIGSTOP)一次 search 要 68 s 才抛错,FallbackStore 和关键词
兜底都来不及接。这里钉住:服务模式默认给读方法带 timeout,写方法不带,Lite 默认不变。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rag.store import milvus_store as ms  # noqa: E402

try:
    import pymilvus  # noqa: F401

    HAS_MILVUS = True
except ImportError:  # pragma: no cover - CI 默认不装 pymilvus
    HAS_MILVUS = False


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        def fn(*args, **kwargs):
            self.calls.append((name, kwargs))
            if name == "query":
                return [{"count(*)": 0}]
            return None

        return fn


class RpcTimeoutConfigTests(unittest.TestCase):
    def test_server_mode_defaults_and_lite_unchanged(self) -> None:
        with patch.dict(os.environ, {"RAG_MILVUS_TIMEOUT_S": "", "RAG_MILVUS_LOAD_TIMEOUT_S": ""}):
            self.assertEqual(ms._rpc_timeouts(is_lite=False), (5.0, 60.0))
            self.assertEqual(ms._rpc_timeouts(is_lite=True), (None, None))

    def test_env_overrides_and_zero_disables(self) -> None:
        with patch.dict(os.environ, {"RAG_MILVUS_TIMEOUT_S": "2.5", "RAG_MILVUS_LOAD_TIMEOUT_S": "0"}):
            self.assertEqual(ms._rpc_timeouts(is_lite=False), (2.5, None))
            self.assertEqual(ms._rpc_timeouts(is_lite=True), (2.5, None))  # 显式设置对 Lite 也生效
        with patch.dict(os.environ, {"RAG_MILVUS_TIMEOUT_S": "abc", "RAG_MILVUS_LOAD_TIMEOUT_S": ""}):
            self.assertEqual(ms._rpc_timeouts(is_lite=False), (5.0, 60.0))

    def test_proxy_adds_timeout_to_reads_only(self) -> None:
        raw = _Recorder()
        client = ms._TimeoutClient(raw, 5.0, 60.0)
        client.search("c", data=[[0.1]], limit=3)
        client.query("c", filter="", output_fields=["count(*)"])
        client.has_collection("c")
        client.describe_alias("a")
        client.load_collection("c")
        client.query("c", timeout=1.0)  # 调用方显式给了就不覆盖
        client.upsert("c", data=[])
        client.flush("c")
        client.alter_alias("p", "a")
        got = {(name, kw.get("timeout")) for name, kw in raw.calls}
        self.assertIn(("search", 5.0), got)
        self.assertIn(("query", 5.0), got)
        self.assertIn(("has_collection", 5.0), got)
        self.assertIn(("describe_alias", 5.0), got)
        self.assertIn(("load_collection", 60.0), got)
        self.assertIn(("query", 1.0), got)
        for write in ("upsert", "flush", "alter_alias"):
            self.assertIn((write, None), got)


@unittest.skipUnless(HAS_MILVUS, "pymilvus not installed")
class ClientConstructionTests(unittest.TestCase):
    def _build(self, uri: str, env: dict):
        captured: dict = {}

        class FakeMilvusClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch.dict(os.environ, env), patch("pymilvus.MilvusClient", FakeMilvusClient):
            st = ms.MilvusStore(uri=uri)
            client = st._client()
        return client, captured

    def test_server_uri_gets_the_timeout_proxy_and_connect_timeout(self) -> None:
        client, kw = self._build("http://127.0.0.1:19530", {"RAG_MILVUS_TIMEOUT_S": "", "RAG_MILVUS_LOAD_TIMEOUT_S": ""})
        self.assertIsInstance(client, ms._TimeoutClient)
        self.assertEqual(kw.get("timeout"), 5.0)

    def test_server_uri_with_timeouts_disabled_is_the_raw_client(self) -> None:
        client, kw = self._build("http://127.0.0.1:19530", {"RAG_MILVUS_TIMEOUT_S": "0", "RAG_MILVUS_LOAD_TIMEOUT_S": "0"})
        self.assertNotIsInstance(client, ms._TimeoutClient)
        self.assertNotIn("timeout", kw)

    def test_lite_file_default_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:  # _client() 会建父目录;FakeMilvusClient 不会真的打开
            client, kw = self._build(str(Path(tmp) / "never-created.db"),
                                     {"RAG_MILVUS_TIMEOUT_S": "", "RAG_MILVUS_LOAD_TIMEOUT_S": ""})
        self.assertNotIsInstance(client, ms._TimeoutClient)
        self.assertNotIn("timeout", kw)


class ZeroCountCacheTests(unittest.TestCase):
    def test_cached_zero_is_rechecked(self) -> None:
        # 别名被别的进程切到一份新数据后,逻辑名下缓存的 0 会让 _search 直接返回空——不能信
        st = ms.MilvusStore(uri="http://fake:19530")
        raw = _Recorder()
        st._client_obj = raw
        st._loaded.add("products_text")
        self.assertEqual(st._count_where("products_text", 'brand == "X"'), 0)
        self.assertEqual(st._count_where("products_text", 'brand == "X"'), 0)
        self.assertEqual(sum(1 for n, _ in raw.calls if n == "query"), 2)

    def test_cached_positive_is_reused(self) -> None:
        st = ms.MilvusStore(uri="http://fake:19530")
        raw = _Recorder()
        st._client_obj = raw
        st._count_cache[("products_text", 'brand == "X"')] = (10**12, 7)
        self.assertEqual(st._count_where("products_text", 'brand == "X"'), 7)
        self.assertEqual(raw.calls, [])


if __name__ == "__main__":
    unittest.main()
