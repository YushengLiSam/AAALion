"""拍照找货 + 文字融合(IMAGE_TEXT_FUSION)的单测。

全部离线:CLIP / 向量库 / 交叉编码器 / 汇率接口都打桩,商品用真实目录 JSON。
覆盖 rag.retrieve.image_fusion 的每个分支、rag_client 的回退编排、
query.query_images 的多图合并,以及 /chat/stream 图片分支(语言、附加段、开关、异常回退)。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest

from rag.retrieve import image_fusion as F
from rag.retrieve.constraints import build_retrieval_filter
from rag.retrieve.query import Filter, Hit, _product_index

CAT = _product_index()
AIRPODS3 = "p_digital_018"      # Apple 苹果 真无线耳机 ¥1899 US
FREEBUDS = "p_digital_007"      # 华为 真无线耳机 ¥1699 CN
AIRPODS_US = "p_2_intl_02"      # Apple 真无线降噪耳机 $249 US
SONY = "p_2_intl_01"            # Sony 无线降噪耳机 $398 JP
CLEANSER = "p_beauty_011"       # 珊珂 洁面 ¥52 JP(跨品类)


def _norm(products):
    """假汇率:USD×7。与线上 normalize_product_prices 一样返回带 price_cny 的拷贝。"""
    out = []
    for p in products:
        q = json.loads(json.dumps(p))
        cur = str((q.get("provenance") or {}).get("currency") or "CNY").upper()
        q["price_cny"] = round(float(q["base_price"]) * (7.0 if cur == "USD" else 1.0), 2)
        out.append(q)
    return out


def _hits(*pairs):
    return [Hit(pid, sim, CAT[pid]) for pid, sim in pairs]


BASE = _hits((AIRPODS3, 0.80), (FREEBUDS, 0.78), (AIRPODS_US, 0.76), (SONY, 0.70), (CLEANSER, 0.60))


def _fuse(text="", hits=BASE, conv=None, **kw):
    turn = build_retrieval_filter(F.normalize_question_forms(text))
    kw.setdefault("floor", 0.51)
    kw.setdefault("rerank_fn", lambda q, ps: {})
    return F.fuse_image_candidates(hits, text, turn_filter=turn,
                                   conversation_filter=conv if conv is not None else turn,
                                   normalize_fn=_norm, **kw)


def _ids(res):
    return [p["product_id"] for p in res.products]


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """任何代码路径敢发 HTTP 请求(LLM / 汇率)就让测试失败。

    只抛异常不够:extract_negation / rewrite_query / _heavy_retrieve 都把异常吞掉了
    (fail-soft),抛出去的 AssertionError 会被静默吃掉。所以同时**记录**每次调用,
    测试结束时断言一次都没有。"""
    import urllib.request

    calls = []

    def _boom(*a, **k):
        calls.append(getattr(a[0], "full_url", a[0]) if a else k)
        raise AssertionError("network/LLM call attempted in image fusion test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.delenv("IMAGE_TEXT_FUSION", raising=False)
    monkeypatch.delenv("IMAGE_MIN_SIM", raising=False)
    monkeypatch.delenv("IMAGE_CROSS_CAT_MARGIN", raising=False)
    yield calls
    assert not calls, f"network/LLM call attempted (swallowed by fail-soft code): {calls}"


# ---------------------------------------------------------------------------
# 合并 / 下限 / 品类钉住
# ---------------------------------------------------------------------------


def test_merge_visual_hits_takes_max_per_product():
    merged = F.merge_visual_hits([
        _hits((AIRPODS3, 0.6), (FREEBUDS, 0.7)),
        [(AIRPODS3, 0.9, CAT[AIRPODS3]), (SONY, 0.5, CAT[SONY])],
    ])
    assert [(c.product_id, c.sim) for c in merged] == [(AIRPODS3, 0.9), (FREEBUDS, 0.7), (SONY, 0.5)]


def test_no_visual_hits():
    res = _fuse(hits=[])
    assert res.status == "no_visual" and res.products == []


def test_below_floor_returns_no_cards():
    res = _fuse(hits=_hits((AIRPODS3, 0.45), (FREEBUDS, 0.44)))
    assert res.status == "below_floor" and res.products == []
    assert res.top_sim == pytest.approx(0.45)


def test_floor_env_default(monkeypatch):
    assert F.min_sim() == F.DEFAULT_MIN_SIM
    monkeypatch.setenv("IMAGE_MIN_SIM", "0.9")
    assert F.min_sim() == 0.9
    res = F.fuse_image_candidates(BASE, "", normalize_fn=_norm, rerank_fn=lambda q, p: {})
    assert res.status == "below_floor"


def test_image_only_keeps_visual_order_top_k_and_copies():
    before = json.dumps(CAT[AIRPODS3], sort_keys=True)
    res = _fuse("")
    assert res.status == "visual"
    assert _ids(res) == [AIRPODS3, FREEBUDS, AIRPODS_US]
    assert res.enforced == [] and not res.reordered_by_text
    assert res.products[0]["_retrieval"]["clip_sim"] == 0.8
    assert res.products[0]["price_cny"] == 1899.0
    # 目录共享 dict 不能被改动
    assert json.dumps(CAT[AIRPODS3], sort_keys=True) == before


def test_per_candidate_floor_and_k():
    res = _fuse("", k=5)
    assert CLEANSER not in _ids(res)            # 跨品类被钉住
    res = _fuse("", k=5, floor=0.75)
    assert _ids(res) == [AIRPODS3, FREEBUDS, AIRPODS_US]


def test_category_pin_near_tie_and_disable(monkeypatch):
    hits = _hits((AIRPODS3, 0.80), (CLEANSER, 0.79), (FREEBUDS, 0.70))
    assert _ids(_fuse("", hits=hits)) == [AIRPODS3, CLEANSER, FREEBUDS]   # 近似并列保留
    hits = _hits((AIRPODS3, 0.80), (CLEANSER, 0.70), (FREEBUDS, 0.69))
    assert _ids(_fuse("", hits=hits)) == [AIRPODS3, FREEBUDS]
    monkeypatch.setenv("IMAGE_CROSS_CAT_MARGIN", "-1")
    assert _ids(_fuse("", hits=hits)) == [AIRPODS3, CLEANSER, FREEBUDS]


def test_alternative_request_pins_to_anchor_kind():
    # 要替代品(这里是"便宜点")时只留同一种东西:Switch($349.99×7≈¥2450)同属数码电子、
    # 也比 Sony(¥2786)便宜,但不是耳机,不出;FreeBuds(¥1699)是耳机,出
    switch = "p_2_intl_03"
    hits = _hits((SONY, 0.80), (switch, 0.79), (FREEBUDS, 0.70))
    res = _fuse("这个有没有便宜点的", hits=hits, k=5)
    assert _ids(res) == [FREEBUDS]
    # 纯图(不要替代品)时,同大类的候选照常保留
    assert _ids(_fuse("", hits=hits, k=5)) == [SONY, switch, FREEBUDS]
    assert F.anchor_kind(CAT[SONY]) == ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]
    assert F.anchor_kind({"sub_category": "跑步鞋"}) == ["跑步鞋"]   # 宽组(鞋子)不并


def test_category_pin_skipped_when_text_names_category():
    hits = _hits((AIRPODS3, 0.80), (CLEANSER, 0.70))
    res = _fuse("有没有类似的洗面奶", hits=hits)
    assert _ids(res) == [CLEANSER]


# ---------------------------------------------------------------------------
# 硬约束
# ---------------------------------------------------------------------------


def test_explicit_budget_strict_cny():
    res = _fuse("这个有没有1800元以内的", k=5)
    # AirPods 3 ¥1899 出局;美版 AirPods $249×7=¥1743 留下;Sony $398×7=¥2786 出局
    assert _ids(res) == [FREEBUDS, AIRPODS_US]
    assert any("1800" in s for s in res.enforced)
    assert res.anchor_excluded


def test_relative_cheaper_uses_anchor_price():
    res = _fuse("这个有没有便宜点的", k=5)
    assert res.status == "visual"
    assert _ids(res) == [FREEBUDS, AIRPODS_US]
    assert all(p["price_cny"] < 1899 for p in res.products)
    assert any("更便宜" in s and "1899" in s for s in res.enforced)


def test_relative_pricier():
    assert F.relative_price_direction("有没有贵一点的") == "pricier"
    assert F.relative_price_direction("这个太贵了") == "cheaper"
    assert F.relative_price_direction("太便宜了") == "pricier"
    assert F.relative_price_direction("便宜点还是贵点") is None
    res = _fuse("有没有更高端的", k=5)
    assert _ids(res) == [SONY]


def test_explicit_number_beats_relative():
    res = _fuse("有没有便宜点的,2000以内", k=5)
    assert set(_ids(res)) == {AIRPODS3, FREEBUDS, AIRPODS_US}


def test_exclude_brand_with_alias():
    res = _fuse("有没有类似的,不要苹果的", k=5)
    assert _ids(res) == [FREEBUDS, SONY]
    assert any("排除品牌" in s for s in res.enforced)


def test_exclude_country():
    res = _fuse("这个有没有类似的,不要日系的", k=5)
    assert SONY not in _ids(res) and AIRPODS3 in _ids(res)
    assert any("日系" in s for s in res.enforced)


def test_requires_domestic_is_strict():
    res = _fuse("有没有国产的", k=5)
    assert _ids(res) == [FREEBUDS]
    assert "只要国产品牌" in res.enforced


def test_except_brands():
    res = _fuse("苹果以外还有吗", k=5)
    assert _ids(res) == [FREEBUDS, SONY]


def test_title_keyword_exclusion_title_only():
    neg = F.local_negation("不要黑色的")
    assert neg.title_keywords == ["黑色"]
    # Sony 标题含"黑色" → 被排除
    res = _fuse("有没有类似的,不要黑色的", k=5)
    assert SONY not in _ids(res)
    # 否定语境里的价格词 / 指代词不当属性词
    assert F.local_negation("不要太贵的").title_keywords == []
    assert F.local_negation("不要这个").title_keywords == []


def test_identification_question_does_not_exclude_or_pin_brand():
    # "是不是苹果的" 不能被读成 "不是苹果" → 排除;也不能把苹果当成 brand_include
    hits = _hits((FREEBUDS, 0.80), (AIRPODS3, 0.78), (AIRPODS_US, 0.76))
    res = _fuse("这是不是苹果的", hits=hits)
    assert _ids(res) == [FREEBUDS, AIRPODS3, AIRPODS_US]
    res = _fuse("这是苹果的吗", hits=hits)
    assert _ids(res) == [FREEBUDS, AIRPODS3, AIRPODS_US]
    # 正向的 "有苹果的吗" 仍是品牌要求
    res = _fuse("有没有苹果的", hits=hits)
    assert _ids(res) == [AIRPODS3, AIRPODS_US]


def test_local_negation_never_calls_llm_even_with_key(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "sk-test-not-real")
    neg = F.local_negation("不要苹果的,也不要日系")
    assert neg.exclude_brands and neg.country_keywords   # autouse 夹具保证没有网络调用


def test_constraints_emptied_builds_fallback():
    res = _fuse("这个有没有500元以内的")
    assert res.status == "constraints_emptied" and res.products == []
    fb = res.fallback_filter
    # 细分品类按"同一种东西"的窄组展开(耳机族)
    assert fb.category == "数码电子" and fb.sub_categories == ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]
    assert fb.price_max_cny == 500
    assert (res.fallback_query or "").startswith("真无线耳机")
    assert res.anchor["product_id"] == AIRPODS3
    assert "不要" not in (res.fallback_query or "")


def test_fallback_relative_and_brand_exclusion_expanded():
    hits = _hits((AIRPODS3, 0.80), (AIRPODS_US, 0.76))
    res = _fuse("有没有便宜点的,不要苹果的", hits=hits)
    assert res.status == "constraints_emptied"
    fb = res.fallback_filter
    assert fb.price_max_cny == pytest.approx(1898.99)
    assert "Apple 苹果" in (fb.brand_exclude or [])   # 展开成目录真实品牌串
    assert "更便宜" in res.fallback_intent


def test_history_category_conflict_drops_inherited_constraints():
    conv = Filter(category="美妆护肤", sub_categories=["洁面"], price_max_cny=100.0,
                  brand_exclude=["华为"], exclude_keywords=["日系"])
    res = _fuse("", conv=conv, k=5)
    assert res.history_dropped
    # 预算 / 品类丢掉,跨轮排除保留:华为、日系(Sony)被排除
    assert _ids(res) == [AIRPODS3, AIRPODS_US]


def test_history_same_category_kept():
    conv = Filter(category="数码电子", price_max_cny=1800.0)
    res = _fuse("", conv=conv, k=5)
    assert not res.history_dropped and _ids(res) == [FREEBUDS, AIRPODS_US]


def test_inherited_brand_include_dropped_when_photo_brand_differs():
    conv = Filter(category="数码电子", brand_include=["华为"])
    hits = _hits((AIRPODS3, 0.80), (FREEBUDS, 0.75))
    res = _fuse("", hits=hits, conv=conv, k=5)
    assert _ids(res) == [AIRPODS3, FREEBUDS]


# ---------------------------------------------------------------------------
# 文字融合排序
# ---------------------------------------------------------------------------


def test_descriptive_intent_detection():
    for t in ("", "这个", "这是什么", "这个多少钱", "有没有同款", "这个有没有便宜点的",
              "有没有类似的,不要苹果的", "500元以内的有吗", "how much is this", "find similar"):
        assert not F.has_descriptive_intent(t), t
    for t in ("有没有黑色的", "适合跑步的有吗", "降噪效果好一点的", "something for running"):
        assert F.has_descriptive_intent(t), t


def test_text_fusion_reorders_near_ties_only():
    calls = []

    def fake_rerank(q, products):
        calls.append(q)
        return {FREEBUDS: 1.0, AIRPODS3: 0.0, AIRPODS_US: 0.0}

    res = _fuse("有没有降噪好的", rerank_fn=fake_rerank, k=5)
    assert calls and "不要" not in calls[0]
    # FreeBuds 0.78 + 0.1 > AirPods3 0.80 → 上升到第一;Sony 0.70 不会被翻到前面
    assert _ids(res)[0] == FREEBUDS and res.reordered_by_text
    assert res.products[0]["_retrieval"]["fused_score"] == pytest.approx(0.88)

    far = _hits((AIRPODS3, 0.90), (FREEBUDS, 0.70))
    res = _fuse("有没有降噪好的", hits=far, rerank_fn=fake_rerank, k=5)
    assert _ids(res) == [AIRPODS3, FREEBUDS] and not res.reordered_by_text


def test_fuse_scores_formula():
    assert F.fuse_scores({"a": 0.8, "b": 0.75}, {"b": 1.0}, 0.1) == pytest.approx({"a": 0.8, "b": 0.85})


def test_deictic_text_skips_rerank_and_rerank_failure_keeps_visual_order():
    def must_not_run(q, p):
        raise AssertionError("deictic text must not trigger rerank")

    assert _ids(_fuse("这个多少钱", rerank_fn=must_not_run)) == [AIRPODS3, FREEBUDS, AIRPODS_US]

    def broken(q, p):
        raise RuntimeError("model missing")

    res = _fuse("有没有黑色的", rerank_fn=broken)
    assert _ids(res) == [AIRPODS3, FREEBUDS, AIRPODS_US] and not res.reordered_by_text


def test_strip_constraint_phrases():
    s = F.strip_constraint_phrases("有没有黑色的,500元以内,不要苹果的")
    assert "苹果" not in s and "500" not in s and "黑色" in s


# ---------------------------------------------------------------------------
# rag_client 编排
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_fx(monkeypatch):
    from app.services import currency
    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate",
                        lambda s, t: currency.ExchangeRate(s, t, 7.0, "2026-10-01"))
    yield
    currency.clear_rate_cache()


def test_fuse_hits_constraints_emptied_runs_text_fallback(monkeypatch, fixed_fx):
    from app.services import rag_client

    calls = []

    def fake_top_k(text, k=5, filters=None, **kw):
        calls.append((text, kw))
        # 故意混进一个违约的(苹果)和一个超价的,验证回退结果被严格复查
        return [dict(CAT[FREEBUDS]), dict(CAT[AIRPODS3]), dict(CAT["p_digital_016"])]

    monkeypatch.setattr(rag_client, "top_k", fake_top_k)
    res = rag_client.image_text_fuse_hits(
        _hits((AIRPODS3, 0.8), (AIRPODS_US, 0.76)), "有没有便宜点的,不要苹果的",
        turn_filter=build_retrieval_filter("有便宜点的,不要苹果的"), rerank_fn=lambda q, p: {})
    assert res.status == "constraints_emptied"
    assert calls and calls[0][1]["skip_topic_switch"] is True
    assert calls[0][1]["conversation_filter"].sub_categories == ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]
    assert "不要" not in calls[0][0]
    assert [p["product_id"] for p in res.products] == [FREEBUDS]


def test_fuse_hits_widens_to_category_when_sub_empty(monkeypatch, fixed_fx):
    from app.services import rag_client

    seen = []

    def fake_top_k(text, k=5, filters=None, **kw):
        flt = kw["conversation_filter"]
        seen.append(flt.sub_categories)
        # 宽一档后给一个贵的(OPPO ¥3299,应被相对价上限剔除)和一个便宜的(FreeBuds ¥1699)
        return [] if flt.sub_categories else [dict(CAT["p_digital_016"]), dict(CAT[FREEBUDS])]

    monkeypatch.setattr(rag_client, "top_k", fake_top_k)
    res = rag_client.image_text_fuse_hits(
        _hits((SONY, 0.8)), "有没有便宜点的", turn_filter=None, rerank_fn=lambda q, p: {})
    assert seen == [["无线降噪耳机", "真无线耳机", "真无线降噪耳机"], None]
    assert res.trace["fallback"] == "category"
    assert [p["product_id"] for p in res.products] == [FREEBUDS]   # Sony $398×7=¥2786 为上限


def test_fuse_hits_visual_path_never_calls_text_search(monkeypatch, fixed_fx):
    from app.services import rag_client

    monkeypatch.setattr(rag_client, "top_k", lambda *a, **k: pytest.fail("text search not expected"))
    res = rag_client.image_text_fuse_hits(BASE, "", rerank_fn=lambda q, p: {})
    assert res.status == "visual" and len(res.products) == 3


def test_image_text_retrieve_caps_images_and_uses_recall_n(monkeypatch, fixed_fx):
    from app.services import rag_client
    import rag.retrieve.query as q

    seen = {}

    def fake_query_images(images, k=20):
        seen["n"], seen["k"] = len(images), k
        return BASE

    monkeypatch.setattr(q, "query_images", fake_query_images)
    monkeypatch.setenv("IMAGE_MAX_QUERY_IMAGES", "2")
    monkeypatch.setenv("IMAGE_RECALL_N", "7")
    monkeypatch.setattr(F, "default_rerank", lambda q, p: {})
    res = rag_client.image_text_retrieve([b"a", b"b", b"c"], "")
    assert seen == {"n": 2, "k": 7} and res.status == "visual"


# ---------------------------------------------------------------------------
# query.query_images
# ---------------------------------------------------------------------------


def test_query_images_merges_by_max_and_survives_one_failure(monkeypatch):
    import rag.ingest.embed_image as ei
    import rag.store as store
    import rag.retrieve.query as q
    from rag.store.base import Hit as StoreHit

    def fake_embed(data):
        if data == b"bad":
            raise RuntimeError("decode error")
        return [1.0] if data == b"one" else [2.0]

    def fake_store_query(vec, k=5):
        if vec == [1.0]:
            return [StoreHit(AIRPODS3, 0.6, {"product_id": AIRPODS3}), StoreHit(SONY, 0.5, {"product_id": SONY})]
        return [StoreHit(AIRPODS3, 0.9, {"product_id": AIRPODS3}), StoreHit("p_unknown", 0.99, {})]

    monkeypatch.setattr(ei, "embed_image_bytes", fake_embed)
    monkeypatch.setattr(store, "query_image", fake_store_query)
    before = q.fallback_stats()["image_query_failed"]
    hits = q.query_images([b"one", b"bad", b"two"], k=5)
    assert [(h.product_id, h.score) for h in hits] == [(AIRPODS3, 0.9), (SONY, 0.5)]
    assert q.fallback_stats()["image_query_failed"] == before + 1
    assert q.query_images([], k=5) == []


# ---------------------------------------------------------------------------
# /chat/stream 图片分支
# ---------------------------------------------------------------------------

_KNOWN_EVENTS = {"product_card", "delta", "done", "claim_summary", "hop_trace", "clarify",
                 "cart_intent", "error"}
_IMG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8not-a-real-jpeg").decode()


class _CaptureProvider:
    name = "capture"
    supports_tools = False

    def __init__(self):
        self.histories = []

    async def stream_chat(self, messages):
        self.histories.append(messages)
        for ch in "好的":
            yield ch


@pytest.fixture
def client(monkeypatch, fixed_fx):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routes import chat as chat_route
    from app.services import rag_client
    from app.services.cache import cache

    cache._d.clear()
    text_calls = []

    def fake_top_k(text, k=5, filters=None, **kw):
        text_calls.append(text)
        return [dict(CAT["p_digital_016"])]

    monkeypatch.setattr(chat_route, "top_k", fake_top_k)
    monkeypatch.setattr(rag_client, "top_k", fake_top_k)
    monkeypatch.setattr(chat_route, "top_k_image",
                        lambda b, k=3: [dict(CAT[SONY]), dict(CAT[CLEANSER])])
    provider = _CaptureProvider()
    monkeypatch.setattr(chat_route, "get_provider", lambda: provider)
    monkeypatch.delenv("AGENT_PATH", raising=False)
    app = FastAPI()
    app.include_router(chat_route.router)
    app.state.retrieval_ready = True
    with TestClient(app) as c:
        c.text_calls = text_calls
        c.provider = provider
        yield c
    cache._d.clear()


def _outcome(status, ids=(), **kw):
    return F.ImageFusionResult(status=status, products=_norm([CAT[i] for i in ids]), **kw)


def _post(client, text=None, history=(), language=None):
    parts = [{"type": "image_url", "image_url": {"url": _IMG}}]
    if text:
        parts.insert(0, {"type": "text", "text": text})
    body = {"messages": [*history, {"role": "user", "content": parts}]}
    if language:
        body["language"] = language
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert {e["type"] for e in events} <= _KNOWN_EVENTS
    assert events[-1]["type"] == "done"
    system = client.provider.histories[-1][0]["content"] if client.provider.histories else ""
    return events, system


def _cards(events):
    return [e["product"]["product_id"] for e in events if e["type"] == "product_card"]


def _patch_outcome(monkeypatch, outcome, sink=None):
    from app.services import rag_client

    def fake(images, text, **kw):
        if sink is not None:
            sink.append((images, text, kw))
        return outcome

    monkeypatch.setattr(rag_client, "image_text_retrieve", fake)


def test_chat_image_only_replies_in_chinese(client, monkeypatch):
    sink = []
    _patch_outcome(monkeypatch, _outcome("visual", [AIRPODS3, FREEBUDS]), sink)
    events, system = _post(client)
    assert _cards(events) == [AIRPODS3, FREEBUDS]
    assert "Respond ONLY in English" not in system
    assert "本轮是拍照找货" in system
    assert sink[0][1] == ""                         # 占位串不会当成文字传进融合
    assert client.text_calls == []


def test_chat_image_only_follows_previous_english_turn_or_ui_language(client, monkeypatch):
    _patch_outcome(monkeypatch, _outcome("visual", [AIRPODS3]))
    _, system = _post(client, history=[{"role": "user", "content": "show me some headphones"},
                                       {"role": "assistant", "content": "Sure."}])
    assert "Respond ONLY in English" in system
    _, system = _post(client, language="en")
    assert "Respond ONLY in English" in system


def test_reply_language_unit():
    from app.routes.chat import _reply_language
    from app.schemas.chat import ChatMessage

    img = ChatMessage(role="user", content=[{"type": "image_url", "image_url": {"url": _IMG}}])
    assert _reply_language(None, "", [img]) == "zh"
    assert _reply_language(None, "what is this", [img]) == "en"
    assert _reply_language(None, "这是什么", [img]) == "zh"
    assert _reply_language("en", "这是什么", [img]) == "en"
    prev = ChatMessage(role="user", content="any cheaper ones?")
    assert _reply_language(None, "", [prev, img]) == "en"
    # 纯文字的空输入保持原行为(中文),不去翻历史
    assert _reply_language(None, "", [prev, ChatMessage(role="user", content=" ")]) == "zh"


def test_chat_visual_addendum_lists_enforced_conditions(client, monkeypatch):
    out = _outcome("visual", [FREEBUDS], enforced=["价格不超过 ¥1800(按人民币)"],
                   anchor=_norm([CAT[AIRPODS3]])[0], anchor_excluded=True)
    _patch_outcome(monkeypatch, out)
    events, system = _post(client, "这个有没有1800元以内的")
    assert _cards(events) == [FREEBUDS]
    assert "强制执行" in system and "1800" in system
    assert "不满足上述条件" in system and "AirPods" in system
    assert client.text_calls == []


def test_chat_below_floor_without_text_has_no_cards(client, monkeypatch):
    _patch_outcome(monkeypatch, _outcome("below_floor", top_sim=0.3))
    events, system = _post(client)
    assert _cards(events) == []
    assert client.text_calls == []
    assert "目录里没有与图片相似的商品" in system


def test_chat_below_floor_with_deictic_text_has_no_cards(client, monkeypatch):
    _patch_outcome(monkeypatch, _outcome("below_floor", top_sim=0.3))
    events, system = _post(client, "这是什么")
    assert _cards(events) == [] and client.text_calls == []


def test_chat_below_floor_with_descriptive_text_uses_text_pipeline(client, monkeypatch):
    _patch_outcome(monkeypatch, _outcome("below_floor", top_sim=0.3))
    events, system = _post(client, "有没有适合拍照的手机")
    assert _cards(events) == ["p_digital_016"]
    assert len(client.text_calls) == 1
    assert "按用户的文字" in system


def test_chat_constraints_emptied_addendum(client, monkeypatch):
    anchor = _norm([CAT[AIRPODS3]])[0]
    _patch_outcome(monkeypatch, _outcome("constraints_emptied", [FREEBUDS], anchor=anchor,
                                         anchor_excluded=True, enforced=["比图中…更便宜"]))
    events, system = _post(client, "这个有没有便宜点的")
    assert _cards(events) == [FREEBUDS] and client.text_calls == []
    assert "同一品类" in system and "AirPods" in system

    _patch_outcome(monkeypatch, _outcome("constraints_emptied", [], anchor=anchor, anchor_excluded=True))
    events, system = _post(client, "这个有没有便宜一些的")
    assert _cards(events) == [] and "本轮没有商品卡" in system


def test_chat_no_visual_falls_back_to_text_search(client, monkeypatch):
    _patch_outcome(monkeypatch, _outcome("no_visual"))
    events, _ = _post(client, "黑色耳机")
    assert _cards(events) == ["p_digital_016"] and len(client.text_calls) == 1


def test_chat_fusion_exception_falls_back_to_plain_clip(client, monkeypatch):
    from app.services import rag_client

    def boom(*a, **k):
        raise RuntimeError("clip exploded")

    monkeypatch.setattr(rag_client, "image_text_retrieve", boom)
    events, system = _post(client, "这个有没有便宜点的")
    assert _cards(events) == [SONY, CLEANSER]      # 旧行为:纯 CLIP top-3
    assert "拍照找货" not in system


def test_chat_flag_off_uses_old_path(client, monkeypatch):
    monkeypatch.setenv("IMAGE_TEXT_FUSION", "0")
    _patch_outcome(monkeypatch, None)
    from app.services import rag_client
    monkeypatch.setattr(rag_client, "image_text_retrieve",
                        lambda *a, **k: pytest.fail("fusion must not run when IMAGE_TEXT_FUSION=0"))
    events, _ = _post(client)
    assert _cards(events) == [SONY, CLEANSER]


def test_chat_passes_raw_text_and_normalized_filters(client, monkeypatch):
    sink = []
    _patch_outcome(monkeypatch, _outcome("visual", [AIRPODS3]), sink)
    _post(client, "这是不是苹果的,500以内")
    images, text, kw = sink[0]
    assert text == "这是不是苹果的,500以内"            # 原文(融合层自己判断辨认式提问)
    assert kw["turn_filter"].brand_exclude is None      # "是不是" 已折叠,不会被读成"不是苹果"
    assert kw["conversation_filter"].price_max_cny == 500
    assert len(images) == 1 and isinstance(images[0], bytes)


# ---------------------------------------------------------------------------
# 复审补充:LLM 零调用的硬保证 / 规则误判
# ---------------------------------------------------------------------------


def _fake_hybrid(monkeypatch, ids):
    from rag.retrieve import hybrid
    from rag.retrieve.hybrid import HybridHit

    monkeypatch.setattr(
        hybrid, "hybrid_topk",
        lambda text, k=10, f=None, **kw: [HybridHit(i, 1.0 / (n + 1), n, n, CAT[i]) for n, i in enumerate(ids)])


def test_top_k_llm_free_never_calls_llm_even_with_key_and_rewrite(monkeypatch, _no_llm):
    from app.services import rag_client

    monkeypatch.setenv("TOKENROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("RAG_REWRITE", "1")
    monkeypatch.setenv("RAG_RERANK", "0")
    monkeypatch.setenv("RAG_PREFERENCES", "0")
    monkeypatch.setattr(rag_client, "_RETRIEVAL_CACHE_ON", False)
    _fake_hybrid(monkeypatch, [AIRPODS3, FREEBUDS, SONY])
    q = "推荐几款耳机,不要苹果的"
    out = rag_client.top_k(q, k=5, relevance_gate=False, llm_free=True)
    ids = [p["product_id"] for p in out]
    assert AIRPODS3 not in ids and FREEBUDS in ids      # 本地否定照样排除苹果
    assert _no_llm == []
    # 对照:同一请求不带 llm_free 时确实会去调 LLM(否定抽取 / 改写)——上面的断言不是空转
    rag_client.top_k(q, k=5, relevance_gate=False)
    assert _no_llm, "control: the default text path is expected to attempt an LLM call here"
    _no_llm.clear()


def test_llm_free_results_do_not_share_retrieval_cache_with_default_path(monkeypatch):
    from app.services import rag_client

    seen = []
    monkeypatch.setattr(rag_client, "_RETRIEVAL_CACHE_ON", True)
    monkeypatch.setattr(rag_client, "_retrieval_cache", {})

    def spy(text, retrieval_filter, preference_text, k, **kw):
        seen.append(kw.get("llm_free"))
        return []

    monkeypatch.setattr(rag_client, "_heavy_retrieve", spy)
    rag_client.top_k("复审缓存隔离探针 耳机", k=5, llm_free=True)
    rag_client.top_k("复审缓存隔离探针 耳机", k=5)
    rag_client.top_k("复审缓存隔离探针 耳机", k=5, llm_free=True)
    assert seen == [True, False]      # 第二次没吃到 llm_free 的缓存;第三次命中自己的条目


def test_fallback_query_never_contains_negation_triggers():
    # "我不是考虑这个颜色" 删掉单字("我/是/这个")后会拼出 "不考虑颜色"——修复前这句会让
    # 回退检索的 top_k 触发 extract_negation(配了 key 就调 LLM)。回退 query 里必须一个否定触发词都没有
    from app.services.rag_client import _negation_signals

    assert _negation_signals(F.descriptive_residual("我不是考虑这个颜色,有没有500元以内的"))
    res = _fuse("我不是考虑这个颜色,有没有500元以内的")
    assert res.status == "constraints_emptied"
    assert not _negation_signals(res.fallback_query or "")
    assert not _negation_signals(res.fallback_intent or "")
    assert F.strip_negation_triggers("不不要要 no logo without box Nokia") == "logo box Nokia"


def test_identification_brand_scope_is_its_own_clause():
    hits = _hits((FREEBUDS, 0.80), (AIRPODS3, 0.78), (AIRPODS_US, 0.76))
    # "这是什么" 是辨认式提问,但 "我只要苹果的" 是真要求,不能一起丢掉
    assert _ids(_fuse("这是什么?我只要苹果的", hits=hits)) == [AIRPODS3, AIRPODS_US]
    # 同一句里被问的品牌(苹果)不当要求,另一分句点名的品牌(华为)照常执行
    assert _ids(_fuse("这是不是苹果的?我只要华为的", hits=hits)) == [FREEBUDS]
    assert F.identification_brands("这是什么?我只要苹果的") == []


def test_quality_words_and_negated_price_words_are_not_price_constraints():
    assert F.relative_price_direction("降噪效果好一点的") is None
    assert F.relative_price_direction("质量好点的") is None
    assert F.relative_price_direction("不要便宜的") is None
    assert F.relative_price_direction("不是要更贵的") is None
    assert F.relative_price_direction("不要太贵的") == "cheaper"      # "太X" 前加"不要"意思不变
    assert F.relative_price_direction("不要太便宜") == "pricier"
    res = _fuse("有没有降噪效果好一点的", k=5)
    assert res.status == "visual" and AIRPODS3 in _ids(res)            # 锚点不会被"更贵"条件筛掉
    assert not any("更贵" in s for s in res.enforced)


def test_fuse_scores_clamps_text_score():
    fused = F.fuse_scores({"a": 0.80, "b": 0.50}, {"b": 7.5, "a": -3.0}, 0.1)
    assert fused == pytest.approx({"a": 0.80, "b": 0.60})               # 文字最多贡献 λ


def test_chat_image_text_fallback_is_llm_free_text_path_unchanged(client, monkeypatch):
    from app.routes import chat as chat_route

    kws = []

    def capture_top_k(text, k=5, filters=None, **kw):
        kws.append(kw)
        return [dict(CAT["p_digital_016"])]

    monkeypatch.setattr(chat_route, "top_k", capture_top_k)
    _patch_outcome(monkeypatch, _outcome("below_floor", top_sim=0.3))
    _post(client, "有没有适合拍照的手机,不要苹果的")
    assert kws and kws[-1]["llm_free"] is True
    # 融合关闭(旧行为)时文字兜底保持原样
    monkeypatch.setenv("IMAGE_TEXT_FUSION", "0")
    monkeypatch.setattr(chat_route, "top_k_image", lambda b, k=3: [])
    _post(client, "有没有适合拍照的手机")
    assert kws[-1]["llm_free"] is False


def test_constraints_emptied_fallback_with_real_top_k_makes_no_llm_call(monkeypatch, fixed_fx, _no_llm):
    # 端到端走真实的 rag_client.top_k(只把混合检索换成固定结果、关掉交叉编码器),
    # 配了 key、开了 RAG_REWRITE:回退检索也不能发出任何 LLM 请求。
    from app.services import rag_client

    monkeypatch.setenv("TOKENROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("RAG_REWRITE", "1")
    monkeypatch.setenv("RAG_RERANK", "0")
    monkeypatch.setattr(rag_client, "_RETRIEVAL_CACHE_ON", False)
    _fake_hybrid(monkeypatch, [FREEBUDS, AIRPODS3, SONY])
    res = rag_client.image_text_fuse_hits(
        _hits((AIRPODS3, 0.8), (AIRPODS_US, 0.76)), "我不是考虑这个颜色,有没有500元以内的",
        turn_filter=build_retrieval_filter("我不是考虑这个颜色,有500元以内的"), rerank_fn=lambda q, p: {})
    assert res.status == "constraints_emptied"
    assert all(p["price_cny"] <= 500 for p in res.products)
    assert _no_llm == []
