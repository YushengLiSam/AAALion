"""多跳检索正确性回归(PLAN.md P0.8)。

Bug 1:外币锚点没做汇率换算。海外版 AirPods Pro 2(p_2_intl_02,base_price=249,
provenance.currency=USD)被当成 ¥249:"比它便宜"的上限算成 236.55,放宽兜底又按
外币数字排序,推出来的全是更贵的。
Bug 2:hop2 的派生 Filter 传给 top_k(conversation_filter=...) 后,被话题切换检测
(派生 Filter 没有 category,目标词"降噪耳机"却有)整个丢掉,派生的价格/品牌
约束到不了 _heavy_retrieve。

全部离线:不加载模型、不连汇率接口(汇率固定 7.0)、不碰向量库。
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

from app.services import currency, rag_client
from app.services.currency import ExchangeRate, price_in_cny
from rag.retrieve.multihop import HopPlan, anchor_attrs, detect_multihop
from rag.retrieve.query import Filter

_FX = 7.0
_SEED = REPO_ROOT / "data" / "seed"


def _catalog(pid: str) -> dict:
    path = next(_SEED.glob(f"*/data/{pid}.json"))
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fixed_fx(monkeypatch):
    """汇率固定为 7.0,且绝不发网络请求。"""
    currency.clear_rate_cache()
    calls = []

    def fake_rate(source, target):
        calls.append((source, target))
        return ExchangeRate(source, target, _FX, "2026-10-01")

    monkeypatch.setattr(currency, "_request_rate", fake_rate)
    yield calls
    currency.clear_rate_cache()


def _cny_item(pid, price, sub="真无线降噪耳机", brand="测试牌"):
    return {"product_id": pid, "title": f"测试耳机 {pid}", "brand": brand,
            "category": "数码电子", "sub_category": sub, "base_price": price,
            "provenance": {"currency": "CNY"}}


# --------------------------------------------------------------------------- #
#  Bug 1 —— 价格口径一律人民币
# --------------------------------------------------------------------------- #

def test_catalog_fixture_is_the_usd_airpods():
    p = _catalog("p_2_intl_02")
    assert p["base_price"] == 249.0
    assert p["provenance"]["currency"] == "USD"


def test_anchor_attrs_never_treats_foreign_base_price_as_cny():
    raw = _catalog("p_2_intl_02")
    assert anchor_attrs(raw)["price_cny"] is None          # 旧实现:249.0
    normalized = currency.normalize_product_price(raw)
    assert anchor_attrs(normalized)["price_cny"] == pytest.approx(249 * _FX)
    # CNY 商品没归一化也能直接用 base_price
    assert anchor_attrs(_cny_item("c1", 999))["price_cny"] == 999.0


def test_price_in_cny_converts_foreign_and_keeps_cny():
    assert price_in_cny(_catalog("p_2_intl_02")) == pytest.approx(249 * _FX)
    assert price_in_cny(_cny_item("c1", 999)) == 999.0
    assert price_in_cny({"base_price": 10, "provenance": {"currency": "USD"},
                         "price_cny": 70.5}) == 70.5


def test_price_in_cny_is_none_when_fx_unavailable(monkeypatch):
    currency.clear_rate_cache()

    def offline(source, target):
        raise RuntimeError("fx offline")

    monkeypatch.setattr(currency, "_request_rate", offline)
    assert price_in_cny(_catalog("p_2_intl_02")) is None   # 绝不回落成 249


class _FakeTopK:
    """按调用顺序返回脚本化结果,并记录每次调用的参数。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, text, k=5, filters=None, **kw):
        self.calls.append({"text": text, "k": k, **kw})
        return [dict(p) for p in (self.script.pop(0) if self.script else [])]


def test_usd_anchor_cheaper_uses_cny_ceiling(monkeypatch):
    anchor = _catalog("p_2_intl_02")
    sony_usd = _catalog("p_2_intl_01")          # 398 USD → ¥2786,比锚点贵
    cheap = _cny_item("c_cheap", 999)           # ¥999,真的更便宜
    fake = _FakeTopK([[anchor], [anchor, sony_usd, cheap]])
    monkeypatch.setattr(rag_client, "top_k", fake)

    plan = detect_multihop("有没有比AirPods Pro便宜的降噪耳机")
    got_anchor, results, trace = rag_client.multi_hop_retrieve(plan, k=4)

    assert trace["anchor"]["price_cny"] == pytest.approx(249 * _FX)
    assert trace["derived_filter"]["price_max_cny"] == pytest.approx(round(249 * _FX * 0.95, 2))
    assert got_anchor["price_cny"] == pytest.approx(249 * _FX)
    # 旧实现:上限 236.55 → ¥999 被剔除 → relaxed;Sony 的 398 当 ¥398
    assert [p["product_id"] for p in results] == ["c_cheap"]
    assert trace["relaxed"] is False


def test_relaxed_fallback_sorts_by_cny_distance(monkeypatch):
    anchor = _catalog("p_2_intl_02")            # ¥1743
    freebuds = _cny_item("c_1699", 1699)        # 距离 44
    airpods3 = _cny_item("c_1899", 1899)        # 距离 156
    sony_usd = _catalog("p_2_intl_01")          # ¥2786,距离 1043(旧实现按 398 vs 249 = 149 排第一)
    bose_usd = _catalog("p_2_intl_04")          # ¥3003
    fake = _FakeTopK([[anchor], [], [sony_usd, bose_usd, airpods3, freebuds]])
    monkeypatch.setattr(rag_client, "top_k", fake)

    plan = detect_multihop("有没有比AirPods Pro便宜的降噪耳机")
    _, results, trace = rag_client.multi_hop_retrieve(plan, k=4)

    assert trace["relaxed"] is True
    assert [p["product_id"] for p in results] == ["c_1699", "c_1899", "p_2_intl_01", "p_2_intl_04"]
    assert all(p.get("price_cny") is not None for p in results)


def test_unknown_anchor_price_aborts_multihop(monkeypatch):
    def offline(source, target):
        raise RuntimeError("fx offline")

    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate", offline)
    fake = _FakeTopK([[_catalog("p_2_intl_02")], [_cny_item("c", 100)]])
    monkeypatch.setattr(rag_client, "top_k", fake)

    anchor, results, trace = rag_client.multi_hop_retrieve(
        detect_multihop("有没有比AirPods Pro便宜的降噪耳机"), k=4)
    assert results == []                        # chat.py 据此回退单跳
    assert trace["fallback"] == "anchor_price_unknown"
    assert len(fake.calls) == 1                 # 没有拿外币数字去做 hop2


def test_assert_relation_compares_cny():
    attrs = {"price_cny": 1743.0}
    f = Filter(price_max_cny=1655.85)
    kept = rag_client._assert_relation(
        [_catalog("p_2_intl_01"), _cny_item("c", 999)], attrs, "cheaper", f)
    assert [p["product_id"] for p in kept] == ["c"]   # Sony 398 USD ≠ ¥398


# --------------------------------------------------------------------------- #
#  Bug 2 —— 派生约束必须到达 _heavy_retrieve
# --------------------------------------------------------------------------- #

@pytest.fixture
def heavy_spy(monkeypatch):
    calls = []

    def spy(text, retrieval_filter, preference_text, k, **kw):
        calls.append({"text": text, "filter": retrieval_filter})
        return []

    monkeypatch.setattr(rag_client, "_heavy_retrieve", spy)
    monkeypatch.setattr(rag_client, "_RETRIEVAL_CACHE_ON", False)
    return calls


def test_hop2_derived_filter_reaches_heavy_retrieve(heavy_spy):
    anchor = _cny_item("c_anchor", 1699, sub="真无线降噪耳机", brand="华为")
    plan = HopPlan(relation="cheaper", anchor_text="", target_text="降噪耳机", anchor_ordinal=1)
    rag_client.multi_hop_retrieve(plan, history_products=[anchor], k=4)

    hop2 = heavy_spy[0]["filter"]               # 第一次 _heavy_retrieve 就是 hop2
    assert isinstance(hop2, Filter)
    assert hop2.price_max_cny == pytest.approx(round(1699 * 0.95, 2))
    assert set(hop2.sub_categories) >= {"真无线降噪耳机", "无线降噪耳机"}


def test_hop2_same_brand_keeps_brand_and_target_category(heavy_spy):
    anchor = {"product_id": "a", "title": "Apple iPhone", "brand": "Apple 苹果",
              "category": "数码电子", "sub_category": "智能手机", "base_price": 8999}
    plan = HopPlan(relation="same_brand", anchor_text="", target_text="平板", anchor_ordinal=1)
    rag_client.multi_hop_retrieve(plan, history_products=[anchor], k=4)

    hop2 = heavy_spy[0]["filter"]
    assert "Apple 苹果" in hop2.brand_include
    assert {"Apple", "苹果"} <= set(hop2.brand_include)   # 目录里同品牌的其它写法
    assert hop2.sub_categories == ["平板电脑"]             # 目标词的品类没丢


def test_single_hop_topic_switch_unchanged(heavy_spy):
    """默认(skip_topic_switch=False)行为不变:继承来的无类目 Filter 遇到新类目词仍被丢弃。"""
    inherited = Filter(price_max_cny=500, sub_categories=["洁面"])
    rag_client.top_k("降噪耳机", conversation_filter=inherited, intent_text="降噪耳机")
    f = heavy_spy[-1]["filter"]
    assert f is None or f.price_max_cny is None


def test_skip_topic_switch_keeps_authoritative_filter(heavy_spy):
    derived = Filter(price_max_cny=500, sub_categories=["无线降噪耳机"])
    rag_client.top_k("降噪耳机", conversation_filter=derived, intent_text="降噪耳机",
                     skip_topic_switch=True)
    assert heavy_spy[-1]["filter"] is derived
