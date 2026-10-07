"""rag/eval/gate.py:逐 case 对比逻辑、设置不一致拒绝比较、LLM 守卫、提交的基线文件。

不跑真实检索(那需要模型和索引,由 CI 的 retrieval-gate job 跑);这里只测纯逻辑。
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

gate = importlib.import_module("rag.eval.gate")
BASELINE = REPO_ROOT / "docs" / "bench" / "eval_baseline.json"


def _doc(cases: list[dict], *, r5: float = 0.9, mrr: float = 0.8, env: dict | None = None) -> dict:
    return {
        "gate_version": gate.GATE_VERSION,
        "meta": {
            "mode": "hybrid_rerank",
            "retrieval_env": env or dict(gate.PRODUCTION_RETRIEVAL_ENV),
            "fixed_fx_to_cny": dict(gate.FIXED_FX),
            "rag_store": "chroma",
            "blocked_llm_calls": 0,
        },
        "sets": {"golden": {"overall": {"recall@5": r5, "mrr": mrr}, "errors": 0, "cases": cases}},
    }


def _case(key: str, hit: bool, *, forbidden=None, clean=None, top=None) -> dict:
    row = {"key": key, "query": f"q-{key}", "expected": ["p1"], "retrieved_top10": top or (["p1"] if hit else ["p9"]),
           "hit": hit, "recall@5": 1.0 if hit else 0.0, "mrr": 1.0 if hit else 0.0}
    if forbidden is not None:
        row["forbidden"] = forbidden
        row["negation_clean"] = clean
    return row


class CompareTests(unittest.TestCase):
    def test_identical_runs_pass(self) -> None:
        doc = _doc([_case("a", True), _case("b", False)])
        report = gate.compare(doc, copy.deepcopy(doc))
        self.assertTrue(report["ok"])
        self.assertEqual(report["failures"], [])

    def test_hit_to_miss_fails_and_is_listed(self) -> None:
        base = _doc([_case("a", True), _case("b", True)])
        cur = _doc([_case("a", True), _case("b", False)], r5=0.9)
        report = gate.compare(base, cur)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["failures"]), 1)
        self.assertIn("hit → miss", report["failures"][0])
        self.assertIn("q-b", report["failures"][0])

    def test_miss_to_hit_is_only_a_note(self) -> None:
        base = _doc([_case("a", False)])
        cur = _doc([_case("a", True)])
        report = gate.compare(base, cur)
        self.assertTrue(report["ok"])
        self.assertTrue(any("miss → hit" in n for n in report["notes"]))

    def test_mean_drop_over_tolerance_fails(self) -> None:
        base = _doc([_case("a", True)], r5=0.90, mrr=0.80)
        self.assertTrue(gate.compare(base, _doc([_case("a", True)], r5=0.885, mrr=0.785))["ok"])  # 0.015
        report = gate.compare(base, _doc([_case("a", True)], r5=0.90, mrr=0.77))  # MRR -0.03
        self.assertFalse(report["ok"])
        self.assertTrue(any("mean mrr dropped" in f for f in report["failures"]))

    def test_exactly_at_tolerance_passes(self) -> None:
        base = _doc([_case("a", True)], r5=0.90)
        self.assertTrue(gate.compare(base, _doc([_case("a", True)], r5=0.88))["ok"])

    def test_negation_leak_fails(self) -> None:
        base = _doc([_case("a", True, forbidden=["p5"], clean=True)])
        cur = _doc([_case("a", True, forbidden=["p5"], clean=False, top=["p1", "p5"])])
        report = gate.compare(base, cur)
        self.assertFalse(report["ok"])
        self.assertIn("negation leak", report["failures"][0])

    def test_settings_mismatch_refuses_to_compare(self) -> None:
        base = _doc([_case("a", True)])
        cur = _doc([_case("a", True)], env={**gate.PRODUCTION_RETRIEVAL_ENV, "RERANK_MAX_LENGTH": "256"})
        report = gate.compare(base, cur)
        self.assertFalse(report["ok"])
        self.assertIsNotNone(report["settings_mismatch"])
        self.assertIn("SETTINGS MISMATCH", gate.render_markdown(report))

    def test_errors_and_missing_sets_fail_new_cases_are_notes(self) -> None:
        base = _doc([_case("a", True)])
        cur = _doc([_case("a", True), _case("new", False)])
        cur["sets"]["golden"]["errors"] = 2
        report = gate.compare(base, cur)
        self.assertFalse(report["ok"])
        self.assertTrue(any("raised errors" in f for f in report["failures"]))
        self.assertTrue(any("new case" in n for n in report["notes"]))
        cur2 = _doc([_case("a", True)])
        cur2["sets"] = {}
        self.assertFalse(gate.compare(base, cur2)["ok"])

    def test_blocked_llm_calls_fail(self) -> None:
        base = _doc([_case("a", True)])
        cur = _doc([_case("a", True)])
        cur["meta"]["blocked_llm_calls"] = 1
        self.assertFalse(gate.compare(base, cur)["ok"])

    def test_cli_exit_codes(self) -> None:
        import tempfile

        base = _doc([_case("a", True)])
        with tempfile.TemporaryDirectory() as d:
            b, c = Path(d) / "b.json", Path(d) / "c.json"
            b.write_text(json.dumps(base))
            c.write_text(json.dumps(base))
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(Path(d) / "summary.md")}), \
                    patch("sys.stdout"):
                self.assertEqual(gate.main(["compare", "--baseline", str(b), "--current", str(c)]), 0)
                c.write_text(json.dumps(_doc([_case("a", False)])))
                self.assertEqual(gate.main(["compare", "--baseline", str(b), "--current", str(c)]), 1)
                c.write_text(json.dumps(_doc([_case("a", True)], env={"RERANK_INPUT_CAP": "0"})))
                self.assertEqual(gate.main(["compare", "--baseline", str(b), "--current", str(c)]), 2)
            self.assertIn("Retrieval eval gate", (Path(d) / "summary.md").read_text())


class CaseKeyTests(unittest.TestCase):
    def test_keys_are_stable_and_deduplicated(self) -> None:
        seen: dict[str, int] = {}
        k1 = gate.case_key({"query": "推荐耳机"}, seen)
        k2 = gate.case_key({"query": "推荐耳机"}, seen)
        self.assertEqual(k2, f"{k1}#1")
        self.assertEqual(gate.case_key({"query": "推荐耳机"}, {}), k1)
        self.assertEqual(gate.case_key({"id": "c42", "query": "x"}, {}), "c42")


class LlmGuardTests(unittest.TestCase):
    def test_check_url_blocks_llm_gateways_only(self) -> None:
        before = len(gate._blocked_llm_calls)
        for url in ("https://api.tokenrouter.com/v1/chat/completions",
                    "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                    "https://api.anthropic.com/v1/messages"):
            with self.assertRaises(RuntimeError):
                gate._check_url(url)
        gate._check_url("https://huggingface.co/BAAI/bge-reranker-base/resolve/main/config.json")
        gate._check_url("https://api.frankfurter.dev/v2/rate/USD/CNY")
        self.assertEqual(len(gate._blocked_llm_calls) - before, 3)
        del gate._blocked_llm_calls[before:]

    def test_retrieval_llm_paths_are_skipped_without_key(self) -> None:
        """negation / rewrite 是检索链路里仅有的两个 LLM 调用点;key 为空串时都不发请求。"""
        from rag.retrieve import negation, rewrite

        with patch.dict(os.environ, {"TOKENROUTER_API_KEY": ""}), \
                patch("urllib.request.urlopen", side_effect=AssertionError("network call")) as uo:
            out = negation.extract_negation("推荐防晒霜,不要日系品牌")
            self.assertIn("exclude_keywords", out)
            rewrite.rewrite_query("推荐耳机")
            uo.assert_not_called()


class BaselineFileTests(unittest.TestCase):
    def test_committed_baseline_matches_gate_settings(self) -> None:
        doc = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(doc["gate_version"], gate.GATE_VERSION)
        meta = doc["meta"]
        self.assertEqual(meta["mode"], gate.GATE_MODE)
        self.assertEqual(meta["retrieval_env"], gate.PRODUCTION_RETRIEVAL_ENV)
        self.assertEqual(meta["retrieval_env"]["RERANK_INPUT_CAP"], "10")
        self.assertEqual(meta["retrieval_env"]["RERANK_MAX_LENGTH"], "128")
        self.assertEqual(meta["fixed_fx_to_cny"], gate.FIXED_FX)
        self.assertEqual(meta["device"], "cpu")
        self.assertEqual(meta["blocked_llm_calls"], 0)
        self.assertEqual(set(doc["sets"]), set(gate.GOLDEN_SETS))
        for name, s in doc["sets"].items():
            self.assertEqual(s["errors"], 0, name)
            from rag.eval.core import load_cases

            self.assertEqual(s["n_cases"], len(load_cases(gate.GOLDEN_SETS[name])), name)
            keys = [c["key"] for c in s["cases"]]
            self.assertEqual(len(keys), len(set(keys)))
        # 自己和自己比必须通过
        self.assertTrue(gate.compare(doc, copy.deepcopy(doc))["ok"])


if __name__ == "__main__":
    unittest.main()
