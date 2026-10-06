"""rag/eval/agent_eval.py 的判分逻辑 + 用例数据完整性 + 无模型的小型端到端。

端到端部分用"扫目录"的假检索器代替向量检索(不加载模型),只验证评测管线本身
能跑通、判分口径正确;真实检索效果请跑 `python -m rag.eval.agent_eval`。
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest

from app.agent.tools import TOOL_SPECS
from app.services import currency, rag_client
from app.services.currency import ExchangeRate
from rag.eval import agent_eval as ev
from rag.retrieve.query import product_matches_filter

_SEED = REPO_ROOT / "data" / "seed"
CATALOG = {p["product_id"]: p for p in rag_client._catalog_index()}


@pytest.fixture(autouse=True)
def fixed_fx(monkeypatch):
    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate", lambda s, t: ExchangeRate(s, t, 7.0, "2026-10-01"))
    yield
    currency.clear_rate_cache()


def _p(pid):
    return dict(CATALOG[pid])


# --------------------------------------------------------------------------- #
#  用例数据完整性
# --------------------------------------------------------------------------- #

def test_cases_are_well_formed_and_reference_real_products():
    cases = ev.load_cases()
    assert len(cases) >= 30
    assert len({c["id"] for c in cases}) == len(cases)
    kinds = {c["kind"] for c in cases}
    assert kinds == {"multihop", "usd_anchor", "comparison", "bundle", "cross_currency"}
    golden = [json.loads(l) for l in (REPO_ROOT / "rag" / "eval" / "golden_multihop.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {g["query"] for g in golden} <= {c["query"] for c in cases}   # 原 15 例全部纳入
    for c in cases:
        ids = list(c.get("anchor_ids") or []) + list(c.get("expect_any") or [])
        ids += [i for g in c.get("expect_groups") or [] for i in g]
        missing = [i for i in ids if i not in CATALOG]
        assert not missing, (c["id"], missing)
        for step in c.get("fake_script") or []:
            for item in step:
                assert "submit" in item or item.get("name") in TOOL_SPECS, (c["id"], item)
        if c["kind"] in ("multihop", "usd_anchor"):
            assert c["relation"] in ("cheaper", "pricier", "same_price", "same_brand", "pair")
            assert c["anchor_ids"]
        if c["kind"] == "bundle":
            assert c["bundle_budget_cny"] > 0


def test_usd_anchor_cases_really_have_usd_anchors():
    for c in ev.load_cases():
        if c["kind"] == "usd_anchor":
            assert any((CATALOG[a].get("provenance") or {}).get("currency") == "USD"
                       for a in c["anchor_ids"]), c["id"]


# --------------------------------------------------------------------------- #
#  判分口径
# --------------------------------------------------------------------------- #

def test_relation_is_judged_in_cny():
    anchor = _p("p_2_intl_02")                      # 249 USD → ¥1743
    assert ev.relation_ok("pricier", anchor, _p("p_digital_018"))      # ¥1899 > ¥1743
    assert not ev.relation_ok("pricier", anchor, _p("p_digital_007"))  # ¥1699 —— 旧口径会判"更贵"
    assert ev.relation_ok("same_price", _p("p_2_intl_01"), _p("p_2_intl_04"))  # ¥2786 vs ¥3003


def test_score_multihop_pass_and_fail():
    case = next(c for c in ev.load_cases() if c["id"] == "u01")
    good = ev.score_run(case, [_p("p_2_intl_02"), _p("p_digital_018")], anchor_id="p_2_intl_02")
    assert good["pass"] and good["relation"] == (1, 1)
    bad = ev.score_run(case, [_p("p_2_intl_02"), _p("p_digital_007")], anchor_id="p_2_intl_02")
    assert not bad["pass"] and bad["relation"] == (0, 1)


def test_score_relaxed_only_ok_when_case_accepts_it():
    mh06 = next(c for c in ev.load_cases() if c["id"] == "mh06")
    s = ev.score_run(mh06, [_p("p_digital_004"), _p("p_digital_023")], anchor_id="p_digital_004", relaxed=True)
    assert s["pass"] and s["relation"] == (0, 0)
    mh02 = next(c for c in ev.load_cases() if c["id"] == "mh02")
    assert not ev.score_run(mh02, [_p("p_beauty_023"), _p("p_beauty_010")],
                            anchor_id="p_beauty_023", relaxed=True)["pass"]


def test_score_comparison_needs_every_group():
    c03 = next(c for c in ev.load_cases() if c["id"] == "c03")
    assert ev.score_run(c03, [_p("p_2_intl_01"), _p("p_2_intl_04")])["pass"]
    assert not ev.score_run(c03, [_p("p_2_intl_01"), _p("p_digital_007")])["pass"]


def test_score_bundle_budget_in_cny_and_category_spread():
    b01 = next(c for c in ev.load_cases() if c["id"] == "b01")
    ok = ev.score_run(b01, [_p("p_clothes_010"), _p("p_clothes_020"), _p("p_clothes_023")])
    assert ok["pass"] and ok["bundle_total_cny"] == 999 + 79 + 149
    two_shoes = ev.score_run(b01, [_p("p_clothes_010"), _p("p_clothes_009")])
    assert not two_shoes["pass"]                       # 只有一个品类
    over = ev.score_run(b01, [_p("p_clothes_008"), _p("p_8_real_05"), _p("p_clothes_014")])
    assert not over["pass"]                            # 1399+1399+1198 > 3000


def test_trajectory_checks():
    ok, _ = ev.trajectory_ok({"rounds": 2, "tool_calls": [{"name": "search_products"}]})
    assert ok
    ok, problems = ev.trajectory_ok({"rounds": 4, "tool_calls": [{"name": "rm_rf"}],
                                     "dropped_ids": ["x"]})
    assert not ok and len(problems) == 3


def test_percentile():
    assert ev.percentile([], 0.5) is None
    assert ev.percentile([1, 2, 3, 4, 100], 0.5) == 3
    assert ev.percentile([1, 2, 3, 4, 100], 0.95) == 100


# --------------------------------------------------------------------------- #
#  无模型端到端:假 LLM + 扫目录的假检索
# --------------------------------------------------------------------------- #

def _catalog_scan_top_k(text, k=5, filters=None, *, conversation_filter=None, **kw):
    f = conversation_filter
    pool = [currency.normalize_product_price(p) for p in CATALOG.values()]
    pool = [p for p in pool if f is None or product_matches_filter(p, f, strict_cny_price=True)]
    q = set(text or "")
    pool.sort(key=lambda p: (-len(q & set(p.get("title") or "")), p["product_id"]))
    return pool[:k]


def test_agent_eval_runs_end_to_end_with_fake_llm(monkeypatch):
    monkeypatch.setattr(rag_client, "top_k", _catalog_scan_top_k)
    cases = [c for c in ev.load_cases() if c["id"] in ("b01", "c03")]
    report = ev.evaluate(cases, mode="agent", k_runs=2, fake=True, verbose=False)
    summ = report["paths"]["agent"]
    assert summ["pass_k"] == "2/2"
    assert summ["trajectory_ok"] == "4/4"
    assert summ["llm_calls_per_request"] == 2
    assert summ["tokens_per_request"] > 0
    assert summ["latency_ms_p95"] >= summ["latency_ms_p50"]
