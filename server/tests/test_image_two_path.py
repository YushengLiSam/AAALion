"""拍照找货两路召回(IMAGE_TWO_PATH)的单测:SKU 规格问题、照片当锚点的关系问法、
视觉路 + 文字路的商品级 RRF、钉住规则、fail-soft 回到级联,以及 /chat/stream 的附加段与卡片字段。

全部离线:CLIP / 向量库 / 交叉编码器 / 汇率 / 文字检索(top_k)都打桩,商品用真实目录 JSON。
级联本身(IMAGE_TWO_PATH=0)的单测在 test_image_text_fusion.py。
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
from rag.retrieve import sku_attrs as S
from rag.retrieve.constraints import build_retrieval_filter
from rag.retrieve.multihop import (
    RELATION_PAIR, RELATION_SAME_BRAND, RELATION_SAME_PRICE, detect_photo_relation,
)
from rag.retrieve.query import Filter, Hit, _product_index

CAT = _product_index()
AIRPODS3 = "p_digital_018"      # Apple 苹果 真无线耳机 ¥1899,无颜色 SKU
FREEBUDS = "p_digital_007"      # 华为 真无线耳机 ¥1699,颜色 典雅黑/冰霜银/羽沙白
AIRPODS_US = "p_2_intl_02"      # Apple 真无线降噪耳机 $249
SONY = "p_2_intl_01"            # Sony 无线降噪耳机 $398
OPPO_PHONE = "p_digital_016"    # OPPO 智能手机 ¥3299
HOKA = "p_clothes_009"          # HOKA 跑步鞋 ¥1099,颜色 经典黑/云朵白/雾霾蓝
NIKE_RUN = "p_clothes_007"      # 耐克 跑步鞋 ¥899,只有尺码
ADIDAS_RUN = "p_clothes_008"    # 阿迪达斯 跑步鞋 ¥1399,只有尺码
TEE_UNIQLO = "p_clothes_001"    # 优衣库 短袖T恤 ¥99,黑/白/深蓝,S/M/L
TEE_GREY = "p_clothes_002"      # 优衣库 短袖T恤 ¥129,灰/黑/蓝,M/L/XL


def _norm(products):
    out = []
    for p in products:
        q = json.loads(json.dumps(p))
        cur = str((q.get("provenance") or {}).get("currency") or "CNY").upper()
        q["price_cny"] = round(float(q["base_price"]) * (7.0 if cur == "USD" else 1.0), 2)
        out.append(q)
    return out


def _hits(*pairs):
    return [Hit(pid, sim, CAT[pid]) for pid, sim in pairs]


def _ids(products):
    return [p["product_id"] for p in products]


BASE = _hits((AIRPODS3, 0.80), (FREEBUDS, 0.78), (AIRPODS_US, 0.76), (SONY, 0.70))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """零网络(LLM / 汇率)——fail-soft 代码会吞异常,所以记录调用、结束时断言为空;
    两路相关开关回到默认值;离线图片描述固定成一个可识别的串。"""
    import urllib.request

    calls = []

    def _boom(*a, **k):
        calls.append(getattr(a[0], "full_url", a[0]) if a else k)
        raise AssertionError("network/LLM call attempted")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    for name in ("IMAGE_TWO_PATH", "IMAGE_SKU_ATTRS", "IMAGE_PHOTO_RELATIONS", "IMAGE_TEXT_PATH_PHOTO_ONLY",
                 "IMAGE_PIN_SIM", "IMAGE_TEXT_PATH_K", "IMAGE_MIN_SIM", "IMAGE_CROSS_CAT_MARGIN",
                 "IMAGE_TEXT_FUSION", "IMAGE_TOP_K"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(F, "_caption", lambda pid: f"外观:{pid} 的离线描述" if pid else None)
    yield calls
    assert not calls, f"network/LLM call attempted: {calls}"


@pytest.fixture
def fixed_fx(monkeypatch):
    from app.services import currency
    currency.clear_rate_cache()
    monkeypatch.setattr(currency, "_request_rate",
                        lambda s, t: currency.ExchangeRate(s, t, 7.0, "2026-10-01"))
    yield
    currency.clear_rate_cache()


# ---------------------------------------------------------------------------
# sku_attrs:归一化 / 问法解析 / 三态匹配
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,family", [
    ("经典黑", "黑"), ("深空黑", "黑"), ("暗夜黑实战色", "黑"), ("黑色 Black", "黑"),
    ("黑色白三条纹", "黑"), ("白蓝主场色", "白"), ("藏青色", "蓝"), ("海军蓝", "蓝"),
    ("深空灰", "灰"), ("冰霜银", "银"), ("钛金色", "金"), ("米白色", "米"),
    ("星光色", None), ("午夜色", None), ("经典裸色", None),
])
def test_color_family_is_primary_color_and_conservative(value, family):
    assert S.color_family(value) == family


@pytest.mark.parametrize("text,expected", [
    ("这个有没有黑色的", ("color", "黑", "黑色", False)),
    ("有黑的吗", ("color", "黑", "黑色", False)),
    ("有经典黑吗", ("color", "经典黑", "经典黑", True)),
    ("这个有没有米色的", ("color", "米", "米色", False)),
    ("有没有深蓝色的", ("color", "深蓝色", "深蓝色", True)),
    ("有浅蓝色的吗", ("color", "浅蓝色", "浅蓝色", True)),
    ("有XL码吗", ("size", "XL", "XL码", False)),
    ("有没有2XL的", ("size", "XXL", "XXL码", False)),
    ("有L码吗", ("size", "L", "L码", False)),
    ("42码有吗", ("size", "42", "42码", False)),
    ("有42.5码的吗", ("size", "42.5", "42.5码", False)),
    ("有256G的吗", ("storage", "256GB", "256GB", False)),
    ("有没有1T的", ("storage", "1TB", "1TB", False)),
    ("有没有500ml的", ("capacity", "500ml", "500ml", False)),
    ("有1.5L的吗", ("capacity", "1500ml", "1500ml", False)),
    ("有14寸的吗", ("screen", "14", "14英寸", False)),
    ("黑色的不要,有白的吗", ("color", "白", "白色", False)),     # 后置否定跳过黑,取白
    ("XL码不要,L码有吗", ("size", "L", "L码", False)),
    ("纯黑的有吗", ("color", "黑", "黑色", False)),
    ("有 XL 吗", ("size", "XL", "XL码", False)),
])
def test_parse_attr_ask(text, expected):
    ask = S.parse_attr_ask(text)
    assert ask is not None
    assert (ask.family, ask.value, ask.label, ask.specific) == expected


@pytest.mark.parametrize("text", [
    "", "这个多少钱", "不要黑色的", "别给我白色的", "500元以内的", "我明白的", "有没有便宜点的",
    "这个500g的吗", "LV的包", "有没有M的", "这是什么颜色", "大米的口感",
    # 复审补充:不带"色"字的基础色只在像问颜色的位置算;后置否定;型号里的 XL / XS;运存;T 恤
    "有没有网红款", "这个奶粉的保质期多久", "蛋白粉的效果", "定金的怎么退", "这个黄金的吗",
    "黑色就算了", "白色以外的", "不是黑色的那款", "经典黑的不要",
    "Pixel 7 XL有吗", "iPhone XS 多少钱", "有XL2吗", "有没有64G运存的", "买4T恤",
    "黑科技", "黑头怎么去", "白茶", "ASICS的", "M3芯片的",
])
def test_parse_attr_ask_negatives(text):
    assert S.parse_attr_ask(text) is None


def test_value_normalizers():
    assert S.size_value("XL码") == "XL" and S.size_value("40.5码") == "40.5" and S.size_value("均码") == "均码"
    assert S.size_value("2号") is None
    assert S.storage_value("12GB+256GB") == "256GB" and S.storage_value("512GB SSD") == "512GB"
    assert S.storage_value("1TB") == "1TB" and S.storage_value("标准版") is None
    assert S.capacity_value("50ml 加大装") == "50ml" and S.capacity_value("1.9L") == "1900ml"
    assert S.capacity_value("114g×5袋") is None
    assert S.screen_value("13.2英寸") == "13.2"


def test_product_attr_values_key_families():
    p = {"skus": [
        {"properties": {"颜色": "黑色", "刺绣logo配色": "白勾", "色号": "0.08g 经典色号",
                        "内存容量": "16GB", "固态硬盘容量": "1TB SSD", "裤长": "25英寸",
                        "屏幕尺寸": "14英寸", "容量": "500ml", "规格": "标准"}},
        {"properties": {"颜色": "黑色", "鞋码": "42码"}},
    ]}
    vals = S.product_attr_values(p)
    assert vals["color"] == ["黑色"]                      # logo 配色 / 色号 不算颜色
    assert vals["storage"] == ["1TB SSD"]                 # 内存容量(RAM)不算存储
    assert vals["screen"] == ["14英寸"]                    # 裤长不算屏幕
    assert vals["capacity"] == ["500ml"]                  # "标准" 解析不成体积,不收
    assert vals["size"] == ["42码"]


def test_product_has_three_states_on_catalog():
    black = S.parse_attr_ask("有没有黑色的")
    assert S.product_has(CAT[HOKA], black) is True
    assert S.matching_values(CAT[HOKA], black) == ["经典黑"]
    assert S.product_has(CAT[TEE_GREY], S.parse_attr_ask("有没有紫色的")) is False
    assert S.product_has(CAT[AIRPODS3], black) is None   # 没列颜色 → 不能说没有
    assert S.product_has(CAT[TEE_UNIQLO], S.parse_attr_ask("有XL码吗")) is False
    assert S.product_has(CAT[TEE_GREY], S.parse_attr_ask("有XL码吗")) is True
    assert S.product_has(CAT[TEE_UNIQLO], S.parse_attr_ask("有深蓝色的吗")) is True
    assert S.product_has(CAT["p_clothes_024"], S.parse_attr_ask("有深蓝色的吗")) is False  # 藏青≠深蓝(保守)
    assert S.product_has(CAT["p_digital_002"], S.parse_attr_ask("有256G的吗")) is True


def test_catalog_vocabulary_built_from_catalog():
    vocab = S.catalog_vocabulary()
    assert "经典黑" in vocab["color"] and "XL码" in vocab["size"]
    assert "经典黑" in S._specific_color_names() and "黑色" not in S._specific_color_names()


# ---------------------------------------------------------------------------
# multihop.detect_photo_relation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,relation,target", [
    ("有没有同品牌的", RELATION_SAME_BRAND, ""),
    ("这个牌子还有别的吗", RELATION_SAME_BRAND, ""),
    ("同价位的还有吗", RELATION_SAME_PRICE, ""),
    ("价格差不多的有吗", RELATION_SAME_PRICE, ""),
    ("这个配个什么好", RELATION_PAIR, ""),
    ("怎么搭", RELATION_PAIR, ""),
    ("想配条裤子", RELATION_PAIR, "裤子"),
    ("还有别的同价位的吗", RELATION_SAME_PRICE, ""),            # "别的" 不是否定
    ("有同品牌的吗", RELATION_SAME_BRAND, ""),
    ("同品牌的耳机有吗", RELATION_SAME_BRAND, "耳机"),           # 能解析成品类的词作目标
    ("同价位的跑鞋还有吗", RELATION_SAME_PRICE, "跑鞋"),
    ("同品牌的有没有", RELATION_SAME_BRAND, ""),
    ("搭配一双袜子", RELATION_PAIR, "袜子"),
])
def test_detect_photo_relation(text, relation, target):
    plan = detect_photo_relation(text)
    assert plan is not None and plan.relation == relation and plan.target_text == target
    assert plan.uses_history_anchor and plan.anchor_ordinal == 1


@pytest.mark.parametrize("text", ["", "这个多少钱", "这个配色好看吗", "配置怎么样", "不要同品牌的",
                                  "有没有便宜点的",
                                  # 评价图里这件 / 是非问 / "配件""搭配"作名词,都不是要关系商品
                                  "这个牌子的质量怎么样", "这个牌子的东西靠谱吗", "这个和官网价格一样吗",
                                  "是一个牌子吗", "这两个是同品牌吗", "有配件吗", "配件有哪些",
                                  "这套搭配多少钱"])
def test_detect_photo_relation_negatives(text):
    assert detect_photo_relation(text) is None


# ---------------------------------------------------------------------------
# image_fusion 两路纯函数
# ---------------------------------------------------------------------------


def test_flags_defaults(monkeypatch):
    assert F.two_path_enabled() and F.sku_attrs_enabled() and F.photo_relations_enabled()
    assert F.pin_sim() == F.DEFAULT_PIN_SIM == 0.85
    assert F.text_path_k() == 10 and F.RRF_K == 60
    monkeypatch.setenv("IMAGE_TWO_PATH", "0")
    assert not F.two_path_enabled()


def test_rrf_scores_formula_and_dedupe():
    s = F.rrf_scores([["a", "b", "a"], ["b", "c"]])
    assert s["a"] == pytest.approx(1 / 61)
    assert s["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert s["c"] == pytest.approx(1 / 62)


def test_rrf_merge_ranks_sources_and_tie_break():
    v = [{"product_id": "a", "_retrieval": {"clip_sim": 0.8, "source": "image"}},
         {"product_id": "b", "_retrieval": {"clip_sim": 0.7}}]
    t = [{"product_id": "c", "_retrieval": {"rerank_score": 0.9}},
         {"product_id": "b", "_retrieval": {"rerank_score": 0.5, "dense_rank": 3}}]
    out = F.rrf_merge(v, t, all_sims={"a": 0.8, "b": 0.7, "c": 0.55})
    assert _ids(out) == ["b", "a", "c"]                 # b 在两路里 → 最高;a 与 c 同分,视觉名次优先
    b, a, c = out
    assert (b["_retrieval"]["visual_rank"], b["_retrieval"]["text_rank"]) == (1, 1)
    assert b["_retrieval"]["source"] == "image+text" and b["_retrieval"]["rerank_score"] == 0.5
    assert b["_retrieval"]["dense_rank"] == 3
    assert a["_retrieval"]["text_rank"] is None and a["_retrieval"]["source"] == "image"
    assert c["_retrieval"]["visual_rank"] is None and c["_retrieval"]["clip_sim"] == 0.55
    assert c["_retrieval"]["source"] == "text"
    assert b["_retrieval"]["rrf_score"] == pytest.approx(round(1 / 62 + 1 / 62, 6))
    assert "visual_rank" not in v[0]["_retrieval"]       # 输入不被改动


def test_text_path_query_branches():
    anchor = dict(CAT[HOKA])
    q, src = F.text_path_query("有没有适合跑马拉松的", anchor)
    assert src == "text" and q.startswith("跑步鞋") and "马拉松" in q
    q, src = F.text_path_query("", anchor)
    assert src == "caption" and q == f"外观:{HOKA} 的离线描述"
    q, src = F.text_path_query("这个有没有便宜点的", anchor)
    assert src == "caption"
    q, src = F.text_path_query("", anchor, caption_fn=lambda pid: None)
    assert (q, src) == ("跑步鞋", "head")
    # 用户文字自己点了品类:不加锚点品类名
    q, src = F.text_path_query("有没有适合跑步的T恤", anchor, build_retrieval_filter("有没有适合跑步的T恤"))
    assert not q.startswith("跑步鞋")
    q, _ = F.text_path_query("", anchor, caption_fn=lambda pid: "外观:不含酒精 不要碰水")
    assert "不要" not in q and "不含" not in q


def test_is_deictic_only():
    neg0 = F.LocalNegation()
    assert F.is_deictic_only("这个多少钱", None, neg0, None)
    assert F.is_deictic_only("这是什么", None, neg0, None)
    assert F.is_deictic_only("有没有同款", None, neg0, None)
    assert not F.is_deictic_only("", None, neg0, None)                       # 只发图不算
    assert not F.is_deictic_only("有没有适合跑步的", None, neg0, None)
    assert not F.is_deictic_only("这个有没有便宜点的", None, neg0, "cheaper")
    assert not F.is_deictic_only("这个多少钱", build_retrieval_filter("500以内"), neg0, None)
    assert not F.is_deictic_only("这个多少钱", None, F.LocalNegation(exclude_brands=["苹果"]), None)
    # 辨认式:被问到的品牌不算硬约束,有描述也钉("这是防水的吗"问的就是图里这件)
    q = "这是苹果的吗"
    assert F.is_deictic_only(q, build_retrieval_filter(F.normalize_question_forms(q)), neg0, None)
    assert F.is_deictic_only("这是防水的吗", None, neg0, None)
    q = "这是苹果的吗,500以内"
    assert not F.is_deictic_only(q, build_retrieval_filter(F.normalize_question_forms(q)), neg0, None)
    # 要别的商品:不钉
    for q in ("还有别的吗", "有其他款吗", "换一款"):
        assert not F.is_deictic_only(q, None, neg0, None), q


def test_pin_first_and_hit_sims():
    ps = [{"product_id": x} for x in "abc"]
    out, found = F.pin_first(ps, "c")
    assert _ids(out) == ["c", "a", "b"] and found
    out, found = F.pin_first(ps, "z")
    assert out is ps and not found
    sims = F.hit_sims([Hit("a", 0.5, {}), ("a", 0.7, {}), F.VisualCandidate("b", 0.6, {})])
    assert sims == {"a": 0.7, "b": 0.6}


def test_fuse_without_text_rerank_keeps_visual_order_and_exposes_context():
    def boom(q, ps):
        raise AssertionError("cross-encoder must not run in two-path Path V")

    res = F.fuse_image_candidates(BASE, "有没有降噪好的", normalize_fn=_norm, rerank_fn=boom,
                                  text_rerank=False, k=5)
    assert res.status == "visual" and not res.reordered_by_text
    assert _ids(res.survivors) == [AIRPODS3, FREEBUDS, AIRPODS_US, SONY]
    assert res.sims[AIRPODS3] == 0.8 and res.relative is None


# ---- apply_sku_ask:每个分支(合成商品,精确控制) ----

def _prod(pid, sub="短袖T恤", cat="服饰运动", colors=(), price=100.0):
    return {"product_id": pid, "title": f"T {pid}", "sub_category": sub, "category": cat,
            "base_price": price, "price_cny": price,
            "skus": [{"properties": {"颜色": c}} for c in colors]}


def test_sku_anchor_has_and_survived_pins_first():
    anchor = _prod("a", colors=("经典黑", "白色"))
    ranked = [_prod("b"), anchor]
    out, info, notes = F.apply_sku_ask(ranked, S.parse_attr_ask("有没有黑色的"), anchor, anchor_survived=True)
    assert _ids(out) == ["a", "b"] and info["action"] == "anchor_first" and info["anchor_has"] is True
    assert "有黑色款(来自 SKU 数据" in notes[0] and "经典黑" in notes[0]


def test_sku_anchor_unknown_keeps_ranking():
    anchor = _prod("a")
    ranked = [anchor, _prod("b", colors=("黑色",))]
    out, info, notes = F.apply_sku_ask(ranked, S.parse_attr_ask("有没有黑色的"), anchor, anchor_survived=True)
    assert out == ranked and info["action"] == "anchor_unknown" and info["anchor_has"] is None
    assert "无法确认" in notes[0] and "不要说没有" in notes[0]


def test_sku_anchor_lacks_filters_candidates_same_kind_first():
    anchor = _prod("a", colors=("白色",))
    other_cat = _prod("x", sub="卫衣", colors=("黑色",))
    same = _prod("b", colors=("黑色",))
    ranked = [anchor, other_cat, _prod("c", colors=("红色",)), same]
    out, info, notes = F.apply_sku_ask(ranked, S.parse_attr_ask("有没有黑色的"), anchor, anchor_survived=True)
    assert _ids(out) == ["b", "x"] and info["action"] == "filtered" and info["anchor_has"] is False
    assert "没有黑色款(SKU 数据" in notes[0] and "白色" in notes[0]
    assert "有黑色款的同类" in notes[1]


def test_sku_anchor_has_but_excluded_filters_others():
    anchor = _prod("a", colors=("黑色",))
    ranked = [_prod("b", colors=("黑色",)), _prod("c")]
    out, info, notes = F.apply_sku_ask(ranked, S.parse_attr_ask("有没有黑色的"), anchor, anchor_survived=False)
    assert _ids(out) == ["b"] and info["action"] == "filtered"
    assert "不满足本轮的其他条件" in notes[0]


def test_sku_catalog_scan_when_candidates_lack_value():
    anchor = _prod("a", colors=("白色",))
    catalog = [anchor, _prod("b", colors=("黑色",), price=300), _prod("c", colors=("黑色",), price=50),
               _prod("z", sub="卫衣", cat="鞋", colors=("黑色",))]
    seen = []

    def constraint_fn(ps):
        seen.append(_ids(ps))
        return [p for p in ps if p["price_cny"] <= 100]       # 例如"100 元以内"

    out, info, notes = F.apply_sku_ask([anchor], S.parse_attr_ask("有没有黑色的"), anchor, anchor_survived=True,
                                       catalog=catalog, all_sims={"c": 0.42}, constraint_fn=constraint_fn)
    assert seen == [["b", "c"]]                                # 只扫锚点细分品类族
    assert _ids(out) == ["c"] and info["action"] == "filtered_catalog"
    assert out[0]["_retrieval"] == {"source": "sku_catalog", "clip_sim": 0.42}


def test_sku_none_in_category_keeps_visual_cards():
    anchor = _prod("a", colors=("白色",))
    ranked = [anchor, _prod("b", colors=("红色",))]
    out, info, notes = F.apply_sku_ask(ranked, S.parse_attr_ask("有没有紫色的"), anchor, anchor_survived=True,
                                       catalog=ranked)
    assert out == ranked and info["action"] == "none_in_category"
    assert "同类商品都没有紫色款" in notes[-1]
    # 同类里有,但都不满足其他条件
    catalog = ranked + [_prod("c", colors=("紫色",), price=999)]
    out, info, notes = F.apply_sku_ask(ranked, S.parse_attr_ask("有没有紫色的"), anchor, anchor_survived=True,
                                       catalog=catalog, constraint_fn=lambda ps: [])
    assert out == ranked and info["action"] == "none_in_category" and "都不满足" in notes[-1]


# ---------------------------------------------------------------------------
# rag_client:两路编排
# ---------------------------------------------------------------------------


def _fake_top_k(monkeypatch, results_by_call=None, default=()):
    """rag_client.top_k 打桩:记录每次调用,按调用序号返回预设结果(product_id 列表)。"""
    from app.services import rag_client

    calls = []

    def fake(text, k=5, filters=None, **kw):
        calls.append({"text": text, "k": k, **kw})
        ids = (results_by_call or {}).get(len(calls) - 1, default)
        return [dict(CAT[i]) for i in ids]

    monkeypatch.setattr(rag_client, "top_k", fake)
    return calls


def _two_path(hits, text, **kw):
    from app.services import rag_client
    turn = build_retrieval_filter(F.normalize_question_forms(text))
    kw.setdefault("rerank_fn", lambda q, p: {})
    return rag_client.image_text_fuse_hits(hits, text, turn_filter=turn, conversation_filter=turn, **kw)


def test_photo_only_pins_anchor_by_default(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS_US])
    res = _two_path(BASE, "")
    assert len(calls) == 1                                       # 文字路照样跑(用离线描述)
    assert _ids(res.products) == [AIRPODS3, FREEBUDS, AIRPODS_US]
    assert res.pinned and res.trace["pin"] == "photo_only"


def test_photo_only_runs_text_path_with_caption_and_rrf(monkeypatch, fixed_fx):
    monkeypatch.setenv("IMAGE_PIN_PHOTO_ONLY", "0")
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS_US])
    res = _two_path(BASE, "")
    assert res.two_path and res.status == "visual"
    assert len(calls) == 1
    c = calls[0]
    assert c["llm_free"] is True and c["skip_topic_switch"] is True and c["k"] == 10
    assert c["text"] == f"外观:{AIRPODS3} 的离线描述"
    assert c["conversation_filter"].sub_categories == ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]
    # FREEBUDS: 视觉第 2 + 文字第 1 → 超过只在视觉第 1 的 AirPods(相似度 0.80 < 钉住阈值 0.85)
    assert _ids(res.products) == [FREEBUDS, AIRPODS_US, AIRPODS3]
    sig = res.products[0]["_retrieval"]
    assert (sig["visual_rank"], sig["text_rank"], sig["source"]) == (1, 0, "image+text")
    assert sig["rrf_score"] == pytest.approx(round(1 / 62 + 1 / 61, 6))
    assert res.trace["text_path"]["query_source"] == "caption" and res.trace["pin"] is None
    assert res.reordered_by_text


def test_high_visual_sim_pins_anchor(monkeypatch, fixed_fx):
    _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS_US])
    hits = _hits((AIRPODS3, 0.90), (FREEBUDS, 0.78), (AIRPODS_US, 0.76))
    res = _two_path(hits, "")
    assert _ids(res.products)[0] == AIRPODS3 and res.pinned and res.trace["pin"] == "high_sim"


@pytest.mark.parametrize("text", ["这个多少钱", "这是什么", "有没有同款"])
def test_deictic_question_keeps_top_visual_first(monkeypatch, fixed_fx, text):
    _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS_US])
    res = _two_path(BASE, text)
    assert _ids(res.products)[0] == AIRPODS3 and res.pinned and res.trace["pin"] == "deictic"


def test_identification_question_naming_a_brand_pins_anchor(monkeypatch, fixed_fx):
    # "这是华为的吗"(图里是 AirPods):文字路会把华为排第 1,但问的是图里这件 → 视觉锚点仍在第 1 位
    _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS_US])
    res = _two_path(BASE, "这是华为的吗")
    assert _ids(res.products)[0] == AIRPODS3 and res.pinned and res.trace["pin"] == "deictic"


@pytest.mark.parametrize("text", ["还有别的吗", "有其他款吗"])
def test_asking_for_alternatives_does_not_pin_even_at_high_sim(monkeypatch, fixed_fx, text):
    _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS_US])
    hits = _hits((AIRPODS3, 0.90), (FREEBUDS, 0.78), (AIRPODS_US, 0.76))
    res = _two_path(hits, text)
    assert not res.pinned and res.trace["pin"] is None
    assert _ids(res.products)[0] == FREEBUDS                    # 视觉第 2 + 文字第 1


def test_pinned_flag_false_when_sku_filter_removes_pinned_anchor(monkeypatch, fixed_fx):
    # 相似度过钉住阈值,但锚点 SKU 里没有 XL:锚点被 SKU 过滤拿掉,pinned 不能还是 True
    _fake_top_k(monkeypatch, default=[])
    res = _two_path(_hits((TEE_UNIQLO, 0.90), (TEE_GREY, 0.70)), "这个有没有XL码")
    assert res.sku["action"] == "filtered" and _ids(res.products)[0] == TEE_GREY
    assert not res.pinned and res.trace["pin"] is None


def test_two_path_accepts_one_shot_iterator_hits(monkeypatch, fixed_fx):
    _fake_top_k(monkeypatch, default=[FREEBUDS])
    res = _two_path(iter(BASE), "")
    assert res.two_path and res.status == "visual" and _ids(res.products)[0] == AIRPODS3
    assert res.products[1]["_retrieval"]["clip_sim"] == 0.78    # hit_sims 读到了完整的视觉命中


def test_text_path_results_rechecked_against_constraints_and_category(monkeypatch, fixed_fx):
    # 文字路故意返回:超预算的 AirPods 海外版($249×7)、跨品类的手机、合规的 FreeBuds
    calls = _fake_top_k(monkeypatch, default=[AIRPODS_US, OPPO_PHONE, FREEBUDS])
    res = _two_path(BASE, "这个有没有1800元以内的,不要苹果")
    assert calls and calls[0]["conversation_filter"].price_max_cny == 1800
    for p in res.products:
        assert p["price_cny"] <= 1800 and "苹果" not in p["brand"] and "Apple" not in p["brand"]
        assert p["sub_category"] in ("无线降噪耳机", "真无线耳机", "真无线降噪耳机")
    assert FREEBUDS in _ids(res.products) and OPPO_PHONE not in _ids(res.products)


def test_path_v_only_items_are_above_floor(monkeypatch, fixed_fx):
    _fake_top_k(monkeypatch, default=[])
    hits = _hits((AIRPODS3, 0.80), (FREEBUDS, 0.40))           # FreeBuds 低于下限
    res = _two_path(hits, "", k=5)
    assert _ids(res.products) == [AIRPODS3]


def test_text_path_widens_to_category_when_sub_family_empty(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, results_by_call={0: [], 1: [FREEBUDS]})
    res = _two_path(_hits((SONY, 0.80)), "")
    assert calls[0]["conversation_filter"].sub_categories and calls[1]["conversation_filter"].sub_categories is None
    assert res.trace["text_path"]["scope"] == "category"
    assert _ids(res.products) == [FREEBUDS, SONY] or _ids(res.products) == [SONY, FREEBUDS]


def test_photo_only_text_path_can_be_disabled(monkeypatch, fixed_fx):
    monkeypatch.setenv("IMAGE_TEXT_PATH_PHOTO_ONLY", "0")
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS])
    res = _two_path(BASE, "")
    assert calls == [] and _ids(res.products) == [AIRPODS3, FREEBUDS, AIRPODS_US]
    assert "skipped" in res.trace["text_path"]
    # 有描述性文字时照样跑文字路
    _two_path(BASE, "有没有适合运动的")
    assert len(calls) == 1 and calls[0]["text"].startswith("真无线耳机")


def test_below_floor_and_no_visual_unchanged(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS])
    res = _two_path(_hits((AIRPODS3, 0.40)), "")
    assert res.status == "below_floor" and res.products == [] and res.two_path
    res = _two_path([], "")
    assert res.status == "no_visual" and calls == []


def test_constraints_emptied_still_uses_category_fallback(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS, AIRPODS3])
    res = _two_path(_hits((AIRPODS3, 0.8), (AIRPODS_US, 0.76)), "有没有便宜点的,不要苹果的")
    assert res.status == "constraints_emptied" and res.two_path
    assert len(calls) == 1 and "不要" not in calls[0]["text"]
    assert _ids(res.products) == [FREEBUDS]


def test_two_path_exception_falls_back_to_exact_cascade(monkeypatch, fixed_fx):
    from app.services import rag_client

    def boom(*a, **k):
        raise RuntimeError("text index down")

    monkeypatch.setattr(rag_client, "top_k", boom)
    res = _two_path(BASE, "有没有降噪好的", rerank_fn=lambda q, ps: {FREEBUDS: 1.0})
    monkeypatch.setenv("IMAGE_TWO_PATH", "0")
    ref = _two_path(BASE, "有没有降噪好的", rerank_fn=lambda q, ps: {FREEBUDS: 1.0})
    assert not res.two_path and _ids(res.products) == _ids(ref.products)
    assert res.reordered_by_text and _ids(res.products)[0] == FREEBUDS     # 级联的交叉编码器融合


def test_flag_off_photo_only_never_touches_text_index(monkeypatch, fixed_fx):
    monkeypatch.setenv("IMAGE_TWO_PATH", "0")
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS])
    res = _two_path(BASE, "")
    assert calls == [] and not res.two_path and _ids(res.products) == [AIRPODS3, FREEBUDS, AIRPODS_US]


def test_sku_question_anchor_has_value_is_pinned_with_fact(monkeypatch, fixed_fx):
    _fake_top_k(monkeypatch, default=[NIKE_RUN, ADIDAS_RUN])
    res = _two_path(_hits((HOKA, 0.80), (NIKE_RUN, 0.78), (ADIDAS_RUN, 0.75)), "这个有没有黑色的")
    assert _ids(res.products)[0] == HOKA and res.pinned
    assert res.sku["action"] == "anchor_first"
    assert any("有黑色款(来自 SKU 数据" in n for n in res.notes)


def test_sku_question_anchor_lacks_value_filters(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[])
    res = _two_path(_hits((TEE_UNIQLO, 0.80), (TEE_GREY, 0.70), ("p_clothes_005", 0.69)), "这个有没有XL码")
    assert calls and res.sku["anchor_has"] is False and res.sku["action"] == "filtered"
    assert TEE_UNIQLO not in _ids(res.products)
    assert all(S.product_has(p, S.parse_attr_ask("XL码")) for p in res.products)
    assert any("没有XL码款" in n for n in res.notes)


def test_sku_flag_off_ignores_attribute(monkeypatch, fixed_fx):
    monkeypatch.setenv("IMAGE_SKU_ATTRS", "0")
    _fake_top_k(monkeypatch, default=[])
    res = _two_path(_hits((TEE_UNIQLO, 0.80), (TEE_GREY, 0.70)), "这个有没有XL码")
    assert res.sku is None and res.notes == [] and _ids(res.products)[0] == TEE_UNIQLO


# ---- 照片当锚点 ----

def test_photo_same_brand_reuses_multihop_llm_free(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[AIRPODS_US, FREEBUDS])
    res = _two_path(BASE, "有没有同品牌的")
    assert res.status == "relation" and res.relation == RELATION_SAME_BRAND
    assert calls and all(c["llm_free"] is True for c in calls)
    flt = calls[0]["conversation_filter"]
    assert "Apple 苹果" in flt.brand_include and calls[0]["skip_topic_switch"] is True
    assert _ids(res.products) == [AIRPODS_US]                    # 华为被关系断言剔除,锚点自身不出
    assert res.relation_label.startswith("与「") and "同品牌" in res.relation_label
    assert res.trace["relation"]["hops"][0]["kind"] == "photo_anchor"
    assert res.products[0]["_retrieval"]["source"] == "photo_same_brand"


def test_photo_same_brand_widens_to_category_when_sub_family_has_none(monkeypatch, fixed_fx):
    # 第 1 次(细分品类族):hop2 空(拍照路径不跑多跳的放宽兜底);第 2 次(大类 数码电子):同品牌的 iPhone
    calls = _fake_top_k(monkeypatch, results_by_call={0: [], 1: ["p_digital_001", FREEBUDS]})
    res = _two_path(BASE, "有没有同品牌的")
    assert res.status == "relation" and _ids(res.products) == ["p_digital_001"]
    assert res.trace["relation"]["widened_to_category"] == "数码电子"
    assert len(calls) == 2
    assert calls[1]["text"] == "数码电子" and "Apple 苹果" in calls[1]["conversation_filter"].brand_include
    assert all(c["llm_free"] is True for c in calls)


def test_photo_same_brand_with_named_category_targets_it(monkeypatch, fixed_fx):
    # 拍的是 OPPO 手机,问"同品牌的耳机":目标是耳机族,不是锚点自己的智能手机
    calls = _fake_top_k(monkeypatch, default=[])
    res = _two_path(_hits((OPPO_PHONE, 0.82)), "同品牌的耳机有吗")
    assert res.status == "relation" and res.relation == RELATION_SAME_BRAND
    flt = calls[0]["conversation_filter"]
    assert "真无线耳机" in (flt.sub_categories or []) and "智能手机" not in (flt.sub_categories or [])
    assert "widened_to_category" not in res.trace["relation"]          # 点了品类就不放宽到大类


def test_photo_relation_keeps_turn_constraints(monkeypatch, fixed_fx):
    _fake_top_k(monkeypatch, default=[AIRPODS_US, FREEBUDS])
    res = _two_path(BASE, "有没有同品牌的,1000元以内")
    assert res.status == "relation" and res.products == []        # 海外版 ¥1743 超预算
    assert any("没有满足" in n for n in res.notes)


def test_photo_relation_never_returns_relaxed_results(monkeypatch, fixed_fx):
    # hop2 只给出不满足同价位(±20%)的商品:拍照路径不出"最接近的"放宽卡,也不为它多跑一次检索
    calls = _fake_top_k(monkeypatch, default=[SONY])             # Sony $398×7=¥2786 不在 ¥1519–2279
    res = _two_path(BASE, "同价位的还有吗")
    assert res.status == "relation" and res.relation == RELATION_SAME_PRICE
    assert res.products == [] and not res.trace["relation"].get("relaxed")
    assert len(calls) == 1


def test_multi_hop_text_path_still_relaxes_by_default(monkeypatch, fixed_fx):
    # 文字多跳(allow_relaxed 默认 True)的放宽兜底不受影响
    from app.services import rag_client
    calls = _fake_top_k(monkeypatch, default=[SONY])
    plan = detect_photo_relation("同价位的还有吗")
    _a, found, tr = rag_client.multi_hop_retrieve(plan, history_products=[dict(CAT[AIRPODS3])], k=4)
    assert tr["relaxed"] is True and _ids(found) == [SONY] and len(calls) == 2


def test_photo_pair_uses_pair_map_and_pins_target_subcategories(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS, "p_beauty_011"])
    res = _two_path(_hits((OPPO_PHONE, 0.82)), "这个配个什么")
    assert res.status == "relation" and res.relation == RELATION_PAIR
    assert set(calls[0]["conversation_filter"].sub_categories) >= {"真无线耳机"}
    assert _ids(res.products) == [FREEBUDS]                      # 洁面不在搭配品类里,被钉掉


def test_photo_pair_without_curated_pairing_has_no_cards(monkeypatch, fixed_fx):
    calls = _fake_top_k(monkeypatch, default=[FREEBUDS])
    res = _two_path(BASE, "这个配个什么")                        # 真无线耳机不在 PAIR_MAP
    assert res.status == "relation" and res.products == [] and calls == []
    assert any("搭配表里没有" in n for n in res.notes)


def test_photo_relations_flag_off_falls_back_to_similar_items(monkeypatch, fixed_fx):
    monkeypatch.setenv("IMAGE_PHOTO_RELATIONS", "0")
    _fake_top_k(monkeypatch, default=[])
    res = _two_path(BASE, "有没有同品牌的")
    assert res.status == "visual"


def test_two_path_with_real_top_k_makes_no_llm_call(monkeypatch, fixed_fx, _env):
    """真实 rag_client.top_k(混合检索打桩、关交叉编码器),配了假 key、开 RAG_REWRITE:
    文字路、关系路都不能发出任何请求。"""
    from app.services import rag_client
    from rag.retrieve import hybrid
    from rag.retrieve.hybrid import HybridHit

    # 除了 urllib(_env 已拦),再拦 httpx(汇率 / 其他 HTTP)和 openai 客户端的构造
    import httpx
    import openai

    def _net(*a, **k):
        _env.append(("httpx/openai", a[1:2] if len(a) > 1 else k))
        raise AssertionError("network/LLM call attempted")

    monkeypatch.setattr(httpx.Client, "send", _net)
    monkeypatch.setattr(httpx.AsyncClient, "send", _net)
    monkeypatch.setattr(openai.OpenAI, "__init__", _net)
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", _net)
    monkeypatch.setenv("TOKENROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("RAG_REWRITE", "1")
    monkeypatch.setenv("RAG_RERANK", "0")
    monkeypatch.setattr(rag_client, "_RETRIEVAL_CACHE_ON", False)
    ids = [FREEBUDS, AIRPODS_US, AIRPODS3]
    monkeypatch.setattr(hybrid, "hybrid_topk",
                        lambda text, k=10, f=None, **kw: [HybridHit(i, 1.0 / (n + 1), n, n, CAT[i])
                                                          for n, i in enumerate(ids)])
    res = _two_path(BASE, "有没有适合运动的,不要华为的")
    assert res.two_path and res.status == "visual" and FREEBUDS not in _ids(res.products)
    res = _two_path(BASE, "有没有同品牌的")
    assert res.status == "relation"
    res = _two_path(_hits((HOKA, 0.80), (NIKE_RUN, 0.78)), "这个有没有黑色的")    # SKU 问法
    assert res.two_path and res.sku is not None
    res = _two_path(BASE, "")                                                      # 只发照片(离线描述当 query)
    assert res.two_path and res.trace["text_path"]["query_source"] == "caption"
    assert _env == []


# ---------------------------------------------------------------------------
# /chat/stream
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
        return [dict(CAT[OPPO_PHONE])]

    monkeypatch.setattr(chat_route, "top_k", fake_top_k)
    monkeypatch.setattr(rag_client, "top_k", fake_top_k)
    monkeypatch.setattr(chat_route, "top_k_image", lambda b, k=3: [dict(CAT[SONY])])
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


def _post(client, text=None):
    parts = [{"type": "image_url", "image_url": {"url": _IMG}}]
    if text:
        parts.insert(0, {"type": "text", "text": text})
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": parts}]})
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert {e["type"] for e in events} <= _KNOWN_EVENTS               # 没有新的 SSE 事件类型
    system = client.provider.histories[-1][0]["content"] if client.provider.histories else ""
    return events, system


def _patch_outcome(monkeypatch, outcome):
    from app.services import rag_client
    monkeypatch.setattr(rag_client, "image_text_retrieve", lambda images, text, **kw: outcome)


def _cards(events):
    return [e["product"] for e in events if e["type"] == "product_card"]


def test_chat_two_path_cards_expose_path_ranks_and_facts(client, monkeypatch):
    prods = _norm([CAT[FREEBUDS], CAT[AIRPODS3]])
    prods[0]["_retrieval"] = {"visual_rank": 1, "text_rank": 0, "rrf_score": 0.032657, "clip_sim": 0.78,
                              "source": "image+text", "rerank_score": 0.9}
    prods[1]["_retrieval"] = {"visual_rank": 0, "text_rank": None, "rrf_score": 0.016393, "clip_sim": 0.8,
                              "source": "image"}
    out = F.ImageFusionResult(status="visual", products=prods, two_path=True,
                              anchor=_norm([CAT[AIRPODS3]])[0],
                              notes=["图中商品(视觉最相似的「X」)有黑色款(来自 SKU 数据)。"])
    _patch_outcome(monkeypatch, out)
    events, system = _post(client, "这个有没有黑色的")
    cards = _cards(events)
    assert [c["product_id"] for c in cards] == [FREEBUDS, AIRPODS3]
    sig = cards[0]["retrieval_signals"]
    assert (sig["visual_rank"], sig["text_rank"], sig["rrf_score"], sig["source"]) == (1, 0, 0.032657, "image+text")
    assert cards[1]["retrieval_signals"]["text_rank"] is None
    assert "两路检索融合" in system and "有黑色款(来自 SKU 数据)" in system and "检索层确定的事实" in system
    assert client.text_calls == []


def test_chat_text_only_card_signals_unchanged(client):
    from app.routes.chat import _product_card_event

    p = dict(CAT[OPPO_PHONE])
    p["_retrieval"] = {"rrf_score": 0.03, "dense_rank": 0, "bm25_rank": 1}
    sig = _product_card_event(p)["product"]["retrieval_signals"]
    assert set(sig) == {"rrf_score", "dense_rank", "bm25_rank", "rerank_score", "rerank_rank", "rerank_model"}


def test_chat_relation_status_is_handled_without_text_fallback(client, monkeypatch):
    anchor = _norm([CAT[AIRPODS3]])[0]
    out = F.ImageFusionResult(status="relation", products=_norm([CAT[AIRPODS_US]]), two_path=True,
                              anchor=anchor, relation=RELATION_SAME_BRAND,
                              relation_label="与「AirPods」同品牌(Apple 苹果)",
                              notes=["卡片是与图中商品的关系检索结果"])
    _patch_outcome(monkeypatch, out)
    events, system = _post(client, "有没有同品牌的")
    assert [c["product_id"] for c in _cards(events)] == [AIRPODS_US]
    assert client.text_calls == []
    assert "以图中商品为参照" in system and "同品牌" in system and "不是图中商品本身" in system

    out = F.ImageFusionResult(status="relation", products=[], two_path=True, anchor=anchor,
                              relation=RELATION_PAIR, relation_label="与「AirPods」搭配",
                              notes=["目录的搭配表里没有「真无线耳机」的搭配建议"])
    _patch_outcome(monkeypatch, out)
    events, system = _post(client, "这个配个什么")
    assert _cards(events) == [] and client.text_calls == []
    assert "本轮没有商品卡" in system and "搭配表里没有" in system


def test_chat_cache_does_not_replay_across_two_path_toggle(client, monkeypatch):
    _patch_outcome(monkeypatch, F.ImageFusionResult(status="visual", products=_norm([CAT[AIRPODS3]]),
                                                    two_path=False))
    events, _ = _post(client, "这是什么")
    assert [c["product_id"] for c in _cards(events)] == [AIRPODS3]
    _patch_outcome(monkeypatch, F.ImageFusionResult(status="visual", products=_norm([CAT[FREEBUDS]]),
                                                    two_path=True))
    events, _ = _post(client, "这是什么")
    assert [c["product_id"] for c in _cards(events)] == [FREEBUDS]


def test_chat_cascade_addendum_text_unchanged_without_two_path(client, monkeypatch):
    from app.routes.chat import _image_addendum

    out = F.ImageFusionResult(status="visual", products=[], reordered_by_text=True)
    s = _image_addendum(out, [])
    assert s.startswith("\n\n11. **本轮是拍照找货**: 下面的商品卡是按图片的**视觉相似度**从目录里检索的,"
                        "并结合了用户的文字描述排序。图中商品不一定在目录里")
    assert "检索层确定的事实" not in s
