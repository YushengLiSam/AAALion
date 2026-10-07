"""拍照找货 + 文字 的离线评测(不调用任何 LLM)。

对比两条路径:
  * OLD —— 线上原行为:CLIP 原始 top-3,完全忽略文字,没有相关性下限;
  * NEW —— IMAGE_TEXT_FUSION:server/app/services/rag_client.image_text_fuse_hits
    (视觉召回 top-N → 下限 → 硬约束 → 文字融合 → 约束清空时同类文字回退)。

"用户照片"用目录 145 张商品图做确定性增强(固定随机种子):随机裁剪(边长 70–90%)、
旋转 ±10°、亮度/对比度/饱和度抖动、缩小到长边 224–448、JPEG 质量 ≈60。
注意这只是"同一张图的变体",比真实用户在店里/家里拍的照片容易得多 ——
这里的数字是上限参考,不代表真实拍照找货的效果。

用例:
  (a) 只有图                 → 自身命中@3、同类目精度、平均出卡数
  (b) 图 + "有没有便宜点的"   → 违约率(卡片价 ≥ 原商品价)、同类目率、无卡率
      图 + "这个有没有N元以内的"(N = 原价 × 0.8 取整)→ 违约率(卡片价 > N)
  (c) 图 + "不要{本品牌}"     → 违约率(卡片仍是该品牌);只取本地否定规则认得的品牌
      图 + "不要{日/韩/美…}系" → 违约率(卡片产地仍是该国);只取产地可解析的商品
  (d) 合成无关图(噪声/纯色/渐变/文字/几何图形)→ 拒绝率(一张卡都不出)

--calibrate 额外输出视觉下限 IMAGE_MIN_SIM 的取值依据:增强正样本 top-1 相似度分布
vs 合成负样本 top-1 相似度分布,以及"负样本最大值 + 0.02"建议值下的正样本保留率。

用法(只读共享索引的**拷贝**;Chroma 打开目录时可能写 sqlite,别直接指向线上/共享目录):

    cp -R <repo>/data/.chroma /tmp/chroma_copy
    RAG_CHROMA_DIR=/tmp/chroma_copy python -m rag.eval.image_text_eval --label local --calibrate

汇率固定为 USD→CNY 7.10(离线、可复现;不访问汇率接口)。
结果写到 docs/bench/image_text_eval_<label>.json。
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SEED = 20261007
FIXED_FX = {"USD": 7.10}
COUNTRY_WORD = {"JP": "日系", "KR": "韩系", "US": "美系", "FR": "法系", "DE": "德系", "GB": "英系", "IT": "意系"}


# ---------------------------------------------------------------------------
# 环境:绝不调 LLM、不访问汇率接口
# ---------------------------------------------------------------------------


# 评测期间被拦下的出网请求数(LLM / 汇率 / 任何 urllib 调用);写进结果 JSON 的
# config.llm_calls / network_calls_blocked,是**实测**值而不是写死的 0。
_BLOCKED_NET_CALLS = {"n": 0}


def _hermetic_env() -> None:
    # 先让 app.config 把 server/.env 读进来(若存在),再清 key:否则之后有模块
    # import app.config 时 load_dotenv 会把刚清掉的 TOKENROUTER_API_KEY 又放回来。
    try:
        import app.config  # noqa: F401
    except Exception:
        pass
    for key in ("TOKENROUTER_API_KEY", "DOUBAO_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)
    # 兜底:任何 urllib 出网请求(LLM 抽取 / 改写 / 汇率)直接拒绝并计数。
    import urllib.request

    def _blocked_urlopen(*_a, **_k):
        _BLOCKED_NET_CALLS["n"] += 1
        raise RuntimeError("network disabled in image_text_eval (no LLM / FX calls allowed)")

    urllib.request.urlopen = _blocked_urlopen  # type: ignore[assignment]
    os.environ["RAG_REWRITE"] = "0"
    os.environ.setdefault("RAG_STORE", "chroma")
    os.environ.setdefault("CHROMA_TELEMETRY", "False")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # 评测里不做用户偏好 / 检索缓存以外的个性化
    os.environ.setdefault("RAG_PREFERENCES", "0")

    from app.services import currency

    def _fixed_rate(source: str, target: str):
        rate = FIXED_FX.get(source.upper())
        if rate is None or target.upper() != "CNY":
            raise RuntimeError(f"no fixed FX for {source}/{target}")
        return currency.ExchangeRate(source.upper(), "CNY", rate, "2026-10-07 (fixed for eval)")

    currency.clear_rate_cache()
    currency._request_rate = _fixed_rate  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 图片生成
# ---------------------------------------------------------------------------


def augment(img, rng: random.Random):
    """确定性增强:裁剪 70–90% → 旋转 ±10° → 调色 → 缩小 → JPEG q≈60。返回 JPEG bytes。"""
    from PIL import Image, ImageEnhance

    img = img.convert("RGB")
    w, h = img.size
    frac = rng.uniform(0.70, 0.90)
    cw, ch = max(8, int(w * frac)), max(8, int(h * frac))
    x0 = rng.randint(0, max(0, w - cw))
    y0 = rng.randint(0, max(0, h - ch))
    img = img.crop((x0, y0, x0 + cw, y0 + ch))
    # 旋转后露出的角用边缘像素的平均色填充,避免引入纯白/纯黑大块
    fill = tuple(int(c) for c in img.resize((1, 1)).getpixel((0, 0)))
    img = img.rotate(rng.uniform(-10, 10), resample=Image.BICUBIC, expand=False, fillcolor=fill)
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.8, 1.2))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.8, 1.2))
    img = ImageEnhance.Color(img).enhance(rng.uniform(0.75, 1.25))
    edge = rng.randint(224, 448)
    scale = edge / max(img.size)
    if scale < 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(rng.uniform(55, 65)))
    return buf.getvalue()


def synthetic_negatives(seed: int = SEED) -> list[tuple[str, bytes]]:
    """明显与商品无关的合成图:噪声、纯色、渐变、文字、几何图形。确定性生成。"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    out: list[tuple[str, bytes]] = []
    S = 320

    def _jpeg(img, name):
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        out.append((name, buf.getvalue()))

    for i in range(6):
        _jpeg(Image.fromarray(nrng.integers(0, 256, (S, S, 3), dtype=np.uint8)), f"noise_uniform_{i}")
    for i in range(4):
        g = np.clip(nrng.normal(128, 50, (S, S, 3)), 0, 255).astype(np.uint8)
        _jpeg(Image.fromarray(g), f"noise_gauss_{i}")
    for i in range(4):  # 低频"云"噪声
        small = nrng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
        img = Image.fromarray(small).resize((S, S), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))
        _jpeg(img, f"noise_cloud_{i}")
    solids = [(255, 255, 255), (0, 0, 0), (128, 128, 128), (220, 30, 30), (30, 160, 60),
              (30, 60, 200), (250, 220, 40), (240, 130, 180)]
    for c in solids:
        _jpeg(Image.new("RGB", (S, S), c), f"solid_{c[0]}_{c[1]}_{c[2]}")
    for i in range(6):
        a = np.array([rng.randint(0, 255) for _ in range(3)], dtype=np.float32)
        b = np.array([rng.randint(0, 255) for _ in range(3)], dtype=np.float32)
        t = np.linspace(0, 1, S, dtype=np.float32)
        if i % 3 == 2:  # 径向
            yy, xx = np.mgrid[0:S, 0:S]
            t2 = np.sqrt((xx - S / 2) ** 2 + (yy - S / 2) ** 2) / (S / math.sqrt(2))
            arr = a[None, None, :] * (1 - t2[..., None]) + b[None, None, :] * t2[..., None]
        else:
            line = a[None, :] * (1 - t[:, None]) + b[None, :] * t[:, None]
            arr = np.repeat(line[None, :, :], S, axis=0) if i % 3 == 0 else np.repeat(line[:, None, :], S, axis=1)
        _jpeg(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)), f"gradient_{i}")
    texts = ["Hello world", "Invoice #20481", "Meeting at 3pm", "LOREM IPSUM DOLOR",
             "The quick brown fox", "Q3 revenue report", "404 NOT FOUND", "WiFi password: guest123"]
    for i, t in enumerate(texts):
        img = Image.new("RGB", (S, S), (255, 255, 255) if i % 2 == 0 else (20, 20, 30))
        d = ImageDraw.Draw(img)
        fg = (0, 0, 0) if i % 2 == 0 else (230, 230, 230)
        for row in range(6):
            d.text((12, 20 + row * 48), t, fill=fg)
        _jpeg(img, f"text_{i}")
    for i in range(6):  # 棋盘 / 条纹
        k = rng.choice([8, 16, 32, 40])
        yy, xx = np.mgrid[0:S, 0:S]
        if i % 2 == 0:
            m = ((xx // k + yy // k) % 2).astype(np.float32)
        else:
            m = ((xx // k) % 2).astype(np.float32)
        c1 = np.array([rng.randint(0, 255) for _ in range(3)], dtype=np.float32)
        c2 = np.array([rng.randint(0, 255) for _ in range(3)], dtype=np.float32)
        arr = c1[None, None, :] * m[..., None] + c2[None, None, :] * (1 - m[..., None])
        _jpeg(Image.fromarray(arr.astype(np.uint8)), f"pattern_{i}")
    for i in range(8):  # 随机几何图形
        img = Image.new("RGB", (S, S), tuple(rng.randint(0, 255) for _ in range(3)))
        d = ImageDraw.Draw(img)
        for _ in range(rng.randint(3, 9)):
            x0, y0 = rng.randint(0, S - 40), rng.randint(0, S - 40)
            x1, y1 = x0 + rng.randint(20, 160), y0 + rng.randint(20, 160)
            col = tuple(rng.randint(0, 255) for _ in range(3))
            if rng.random() < 0.5:
                d.ellipse((x0, y0, x1, y1), fill=col)
            else:
                d.rectangle((x0, y0, x1, y1), fill=col)
        _jpeg(img, f"shapes_{i}")
    return out


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


def _visual_hits(jpeg: bytes, k: int):
    from rag.ingest.embed_image import embed_image_bytes
    from rag.retrieve.query import _image_hits
    from rag.store import query_image as store_query_image

    return _image_hits(store_query_image(embed_image_bytes(jpeg), k=k))


def _new_path(hits, text: str):
    """与 chat.py 相同的调用方式:问句规整 → 本轮 Filter + 会话 Filter → 融合。"""
    from app.schemas.chat import ChatMessage
    from app.services.constraint_state import build_conversation_filter
    from app.services.rag_client import image_text_fuse_hits
    from rag.retrieve import image_fusion as F
    from rag.retrieve.constraints import build_retrieval_filter

    text_n = F.normalize_question_forms(text)
    turn = build_retrieval_filter(text_n)
    conv = build_conversation_filter([ChatMessage(role="user", content=text_n)] if text_n else [])
    return image_text_fuse_hits(hits, text, turn_filter=turn, conversation_filter=conv)


def _store_summary() -> dict:
    """只记后端与条数(不记本机路径,结果文件要进公开仓库)。"""
    try:
        from rag.store import backend_name, get_store

        st = get_store()
        return {"backend": backend_name(), "image_count": st.image_count(), "text_count": st.text_count()}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}


def _price(p: dict) -> float | None:
    from app.services.currency import price_in_cny
    return price_in_cny(p)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(mean(xs), 4) if xs else None


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 4)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(label: str, n_aug: int = 3, calibrate: bool = False, limit: int | None = None) -> dict:
    _hermetic_env()
    from PIL import Image

    from app.services.currency import normalize_product_price
    from rag.ingest.embed_image import iter_product_images
    from rag.retrieve import image_fusion as F
    from rag.retrieve.brand_origin import product_origin
    from rag.retrieve.query import _product_index

    catalog = _product_index()
    pairs = [(pid, path) for pid, path in iter_product_images(REPO_ROOT / "data" / "seed") if pid in catalog]
    if limit:
        pairs = pairs[:limit]
    recall = F.recall_n()
    floor = F.min_sim()
    t_start = time.perf_counter()

    # ---- 生成 "用户照片" 并做一次视觉召回(每张图只跑一次 CLIP) ----
    photos: list[dict] = []
    for idx, (pid, path) in enumerate(pairs):
        src = Image.open(path)
        for a in range(n_aug):
            rng = random.Random(SEED * 1000 + idx * 10 + a)
            jpeg = augment(src, rng)
            hits = _visual_hits(jpeg, recall)
            photos.append({"pid": pid, "aug": a, "hits": hits})

    negatives = [{"name": name, "hits": _visual_hits(jpeg, recall)} for name, jpeg in synthetic_negatives()]

    def src(pid):
        return normalize_product_price(catalog[pid])

    # ---- 校准:视觉下限 ----
    pos_top = [ph["hits"][0].score for ph in photos if ph["hits"]]
    pos_self = [next((h.score for h in ph["hits"] if h.product_id == ph["pid"]), None) for ph in photos]
    pos_self_rank1 = sum(1 for ph in photos if ph["hits"] and ph["hits"][0].product_id == ph["pid"]) / max(1, len(photos))
    neg_top = [n["hits"][0].score for n in negatives if n["hits"]]
    suggested = math.ceil((max(neg_top) + 0.02) * 100) / 100 if neg_top else None
    calibration = {
        "positives": {
            "n": len(pos_top),
            "top1_sim": {"min": _pct(pos_top, 0), "p01": _pct(pos_top, 0.01), "p05": _pct(pos_top, 0.05),
                         "p50": _pct(pos_top, 0.5)},
            "self_sim": {"min": _pct([s for s in pos_self if s is not None], 0),
                         "p05": _pct([s for s in pos_self if s is not None], 0.05),
                         "p50": _pct([s for s in pos_self if s is not None], 0.5)},
            "self_is_top1": round(pos_self_rank1, 4),
            "self_missing_from_topN": sum(1 for s in pos_self if s is None),
        },
        "negatives": {
            "n": len(neg_top),
            "top1_sim": {"max": _pct(neg_top, 1.0), "p95": _pct(neg_top, 0.95), "p50": _pct(neg_top, 0.5),
                         "min": _pct(neg_top, 0)},
            "hardest": sorted(({"name": n["name"], "top1": round(n["hits"][0].score, 4),
                                "top1_product": n["hits"][0].product_id} for n in negatives if n["hits"]),
                              key=lambda r: -r["top1"])[:8],
        },
        "suggested_floor(max_neg+0.02)": suggested,
        "configured_floor": floor,
        "positive_retention_at_configured_floor": round(sum(1 for s in pos_top if s >= floor) / max(1, len(pos_top)), 4),
        "negative_rejection_at_configured_floor": round(sum(1 for s in neg_top if s < floor) / max(1, len(neg_top)), 4),
    }
    if suggested is not None:
        calibration["positive_retention_at_suggested_floor"] = round(
            sum(1 for s in pos_top if s >= suggested) / max(1, len(pos_top)), 4)

    results: dict = {}

    # ---- (a) 只有图 ----
    old_a, new_a = [], []
    for ph in photos:
        s = src(ph["pid"])
        for tag, cards, store in (("old", [h.product for h in ph["hits"][:3]], old_a),
                                  ("new", _new_path(ph["hits"], "").products, new_a)):
            ids = [c.get("product_id") for c in cards]
            others = [c for c in cards if c.get("product_id") != ph["pid"]]
            store.append({
                "self_hit@3": 1.0 if ph["pid"] in ids else 0.0,
                "same_cat_precision": (sum(1 for c in cards if c.get("category") == s.get("category")) / len(cards)) if cards else None,
                "same_cat_precision_excl_self": (sum(1 for c in others if c.get("category") == s.get("category")) / len(others)) if others else None,
                "n_cards": len(cards),
            })
    results["a_image_only"] = {
        side: {
            "n": len(rows),
            "self_hit@3": _mean([r["self_hit@3"] for r in rows]),
            "same_cat_precision": _mean([r["same_cat_precision"] for r in rows]),
            "same_cat_precision_excl_self": _mean([r["same_cat_precision_excl_self"] for r in rows]),
            "mean_cards": _mean([r["n_cards"] for r in rows]),
        } for side, rows in (("old", old_a), ("new", new_a))
    }

    first = [ph for ph in photos if ph["aug"] == 0]

    def _score_cases(cases, violates, name):
        rows = {"old": [], "new": []}
        examples = []
        status_counts: dict[str, int] = {}
        for ph, text, ctx in cases:
            s = src(ph["pid"])
            new_res = _new_path(ph["hits"], text)
            status_counts[new_res.status] = status_counts.get(new_res.status, 0) + 1
            for side, cards in (("old", [h.product for h in ph["hits"][:3]]), ("new", new_res.products)):
                cards = [normalize_product_price(c) for c in cards]
                bad = [c for c in cards if violates(c, s, ctx)]
                rows[side].append({
                    "any_violation": 1.0 if bad else 0.0,
                    "card_violation_rate": (len(bad) / len(cards)) if cards else None,
                    "same_cat_rate": (sum(1 for c in cards if c.get("category") == s.get("category")) / len(cards)) if cards else None,
                    "same_sub_rate": (sum(1 for c in cards if c.get("sub_category") == s.get("sub_category")) / len(cards)) if cards else None,
                    "no_cards": 1.0 if not cards else 0.0,
                })
                if side == "new" and bad and len(examples) < 5:
                    examples.append({"pid": ph["pid"], "text": text,
                                     "bad": [c.get("product_id") for c in bad], "status": new_res.status})
        out = {}
        for side, rs in rows.items():
            out[side] = {
                "n": len(rs),
                "violation_rate(any card)": _mean([r["any_violation"] for r in rs]),
                "card_violation_rate": _mean([r["card_violation_rate"] for r in rs]),
                "same_category_rate": _mean([r["same_cat_rate"] for r in rs]),
                "same_sub_category_rate": _mean([r["same_sub_rate"] for r in rs]),
                "no_card_rate": _mean([r["no_cards"] for r in rs]),
            }
        out["new_status_counts"] = status_counts
        out["new_violation_examples"] = examples
        results[name] = out

    # ---- (b) 价格 ----
    def _cheaper_violates(c, s, ctx):
        pc, ps = _price(c), _price(s)
        return pc is None or ps is None or pc >= ps

    _score_cases([(ph, "这个有没有便宜点的", None) for ph in first], _cheaper_violates, "b_cheaper")

    budget_cases = []
    for ph in first:
        ps = _price(src(ph["pid"]))
        if ps is None or ps < 10:
            continue
        n = int(ps * 0.8)
        budget_cases.append((ph, f"这个有没有{n}元以内的", n))

    def _budget_violates(c, s, n):
        pc = _price(c)
        return pc is None or pc > n

    _score_cases(budget_cases, _budget_violates, "b_budget_80pct")

    # ---- (c) 否定 ----
    brand_cases = []
    for ph in first:
        brand = (catalog[ph["pid"]].get("brand") or "").strip()
        tok = brand.replace("（", " ").replace("）", " ").split()[0] if brand else ""
        text = f"有没有类似的，不要{tok}的"
        if tok and F.local_negation(F.normalize_question_forms(text)).exclude_brands:
            brand_cases.append((ph, text, brand))

    def _brand_violates(c, s, brand):
        return F._brand_hit(c, F._alias_terms([brand]))

    _score_cases(brand_cases, _brand_violates, "c_exclude_own_brand")

    country_cases = []
    for ph in first:
        origin = product_origin(catalog[ph["pid"]])
        word = COUNTRY_WORD.get(origin or "")
        if word:
            country_cases.append((ph, f"这个有没有类似的，不要{word}的", origin))

    def _country_violates(c, s, origin):
        return product_origin(c) == origin

    _score_cases(country_cases, _country_violates, "c_exclude_own_country")

    # ---- (d) 合成无关图 ----
    neg_new = [_new_path(n["hits"], "") for n in negatives]
    results["d_synthetic_negatives"] = {
        "old": {"n": len(negatives), "rejection_rate": _mean([0.0 if n["hits"][:3] else 1.0 for n in negatives])},
        "new": {"n": len(negatives), "rejection_rate": _mean([0.0 if r.products else 1.0 for r in neg_new]),
                "status_counts": {st: sum(1 for r in neg_new if r.status == st) for st in {r.status for r in neg_new}}},
    }

    return {
        "label": label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "IMAGE_MIN_SIM": floor,
            "IMAGE_RECALL_N": recall,
            "IMAGE_TOP_K": F.image_top_k(),
            "IMAGE_TEXT_WEIGHT": F.text_weight(),
            "IMAGE_CROSS_CAT_MARGIN": F.cross_cat_margin(),
            "n_catalog_images": len(pairs),
            "n_aug_per_image": n_aug,
            "augmentation": "crop 70-90% side, rotate ±10°, brightness/contrast ±20%, saturation ±25%, "
                            "downscale long edge 224-448, JPEG q55-65",
            "seed": SEED,
            "fx": "USD→CNY 7.10 fixed",
            "store": _store_summary(),
            "llm_calls": _BLOCKED_NET_CALLS["n"],
            "network_calls_blocked": _BLOCKED_NET_CALLS["n"],
            "cases_b_c_use": "augmentation #0 of each image",
            "old_path": "raw CLIP top-3 (same top-N recall list truncated to 3), text ignored, no floor",
        },
        "calibration": calibration if calibrate else {k: calibration[k] for k in (
            "configured_floor", "positive_retention_at_configured_floor", "negative_rejection_at_configured_floor")},
        "results": results,
        "elapsed_s": round(time.perf_counter() - t_start, 1),
    }


# ---------------------------------------------------------------------------
# 两路召回(IMAGE_TWO_PATH)对比:CASCADE(级联,IMAGE_TWO_PATH=0)vs TWO-PATH
# ---------------------------------------------------------------------------
#
# 两边吃**同一份**视觉命中(每张增强图只跑一次 CLIP),直接调用 rag_client._image_cascade /
# _image_two_path(两路模式不经过 fail-soft 包装:出异常就记进 errors,不会悄悄退回级联)。
# 检索缓存关闭(RAG_RETRIEVAL_CACHE=0),延迟是每次真实跑完的耗时。
#
# 用例:
#   (a)  只有图(3 份增强)          → 自身命中@1/@3、同大类精度(含/不含自身)、平均出卡数
#   (a') "这是什么"(辨认式)         → 第 1 张卡是否仍是 CLIP top-1、自身命中@1
#   (b)  图 + SKU 规格问题            → 答案正确率(锚点到底有没有这个值,真值来自商品 JSON 的子串匹配,
#                                      与 sku_attrs 的归一化规则独立)、锚点没有时卡片是否全都有这个值
#   (c)  图 + 同品牌 / 同价位 / 配个什么 → 关系满足率(卡片不含原商品本身)
#   (d)  价格 / 品牌 / 否定(沿用级联评测的四组用例)→ 违约率
#   (e)  延迟:每次融合调用的耗时(不含 CLIP 编码,CLIP 单独统计)


def _sku_truth(product: dict, family: str, value: str) -> bool | None:
    """真值:直接在商品 JSON 的原始 SKU 值上做子串 / 精确匹配(不走 sku_attrs 的归一化)。
    color: 颜色类键的任一原始值含这个颜色字;size: 尺码类键的值恰好是 "{v}码";
    storage: 存储类键的值以 "{v}" 开头或以 "+{v}" 结尾。该商品没有这一族键时为 None。"""
    vals: list[str] = []
    for sku in product.get("skus") or []:
        for key, raw in (sku.get("properties") or {}).items():
            key, raw = str(key), str(raw)
            if family == "color" and "色" in key and "色号" not in key and "logo" not in key.lower():
                vals.append(raw)
            elif family == "size" and "码" in key:
                vals.append(raw)
            elif family == "storage" and ("存储" in key or "硬盘" in key or key == "内存组合"):
                vals.append(raw)
    if not vals:
        return None
    if family == "color":
        return any(value in v for v in vals)
    if family == "size":
        return any(v.strip().upper() == f"{value}码".upper() for v in vals)
    return any(v.replace(" ", "").upper().startswith(value) or v.replace(" ", "").upper().endswith("+" + value)
               for v in vals)


def _sku_question(family: str, value: str) -> str:
    if family == "color":
        return f"这个有没有{value}色的"
    if family == "size":
        return f"这个有{value}码吗"
    return f"这个有{value.replace('GB', 'G')}的吗"


def _sku_values_raw(product: dict, family: str) -> list[str]:
    """生成问句用的候选值(color 取基础色字,size 取码数,storage 取 ROM)。"""
    from rag.retrieve import sku_attrs as S

    out: list[str] = []
    for v in S.product_attr_values(product).get(family, []):
        if family == "color":
            x = S.color_family(v)
        elif family == "size":
            x = S.size_value(v)
            x = None if x == "均码" else x
        else:
            x = S.storage_value(v)
        if x and x not in out:
            out.append(x)
    return out


def _torch_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        return "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "unknown"


def run_two_path(label: str, n_aug: int = 3, limit: int | None = None) -> dict:
    os.environ["RAG_RETRIEVAL_CACHE"] = "0"
    _hermetic_env()
    from PIL import Image

    from app.services import rag_client as RC
    from app.services.currency import normalize_product_price
    from rag.ingest.embed_image import embed_image_bytes, iter_product_images
    from rag.retrieve import image_fusion as F
    from rag.retrieve import sku_attrs as S
    from rag.retrieve.brand_origin import product_origin
    from rag.retrieve.multihop import PAIR_MAP
    from rag.retrieve.query import _image_hits, _product_index
    from rag.store import query_image as store_query_image

    catalog = _product_index()
    pairs = [(pid, path) for pid, path in iter_product_images(REPO_ROOT / "data" / "seed") if pid in catalog]
    if limit:
        pairs = pairs[:limit]
    recall = F.recall_n()
    t_start = time.perf_counter()

    clip_ms: list[float] = []
    photos: list[dict] = []
    for idx, (pid, path) in enumerate(pairs):
        src_img = Image.open(path)
        for a in range(n_aug):
            rng = random.Random(SEED * 1000 + idx * 10 + a)
            jpeg = augment(src_img, rng)
            t0 = time.perf_counter()
            vec = embed_image_bytes(jpeg)
            hits = _image_hits(store_query_image(vec, k=recall))
            clip_ms.append((time.perf_counter() - t0) * 1000)
            photos.append({"pid": pid, "aug": a, "hits": hits})
    first = [ph for ph in photos if ph["aug"] == 0]

    def src(pid):
        return normalize_product_price(catalog[pid])

    from app.schemas.chat import ChatMessage
    from app.services.constraint_state import build_conversation_filter
    from rag.retrieve.constraints import build_retrieval_filter

    errors: list[dict] = []
    latency: dict[str, dict[str, list[float]]] = {}

    # 两路模式的消融变体(只在 (a) 只有图 上跑):环境变量覆盖,跑完恢复
    variants = {
        "two_path[no photo-only pin]": {"IMAGE_PIN_PHOTO_ONLY": "0"},
    }

    def call(side: str, hits, text: str, slice_name: str):
        text_n = F.normalize_question_forms(text)
        turn = build_retrieval_filter(text_n)
        conv = build_conversation_filter([ChatMessage(role="user", content=text_n)] if text_n else [])
        fn = RC._image_cascade if side == "cascade" else RC._image_two_path
        overrides = variants.get(side, {})
        saved = {name: os.environ.get(name) for name in overrides}
        os.environ.update(overrides)
        t0 = time.perf_counter()
        try:
            res = fn(hits, text, turn_filter=turn, conversation_filter=conv)
        except Exception as exc:  # noqa: BLE001
            errors.append({"side": side, "slice": slice_name, "text": text, "error": f"{type(exc).__name__}: {exc}"})
            res = F.ImageFusionResult(status="error")
        finally:
            for name, old in saved.items():
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
        latency.setdefault(slice_name, {}).setdefault(side, []).append((time.perf_counter() - t0) * 1000)
        return res

    sides = ("cascade", "two_path")
    results: dict = {}

    # ---- 钉住阈值的依据 ----
    wrong = sorted(ph["hits"][0].score for ph in photos if ph["hits"] and ph["hits"][0].product_id != ph["pid"])
    pin_cal = {"n": len(photos),
               "clip_top1_is_self": _mean([1.0 if ph["hits"] and ph["hits"][0].product_id == ph["pid"] else 0.0
                                           for ph in photos]),
               "n_top1_wrong": len(wrong),
               "max_sim_when_top1_wrong": round(wrong[-1], 4) if wrong else None,
               "suggested_pin(max_wrong+0.02, ceil 0.01)": (math.ceil((wrong[-1] + 0.02) * 100) / 100) if wrong else None,
               "configured_pin": F.pin_sim()}
    above = [ph for ph in photos if ph["hits"] and ph["hits"][0].score >= F.pin_sim()]
    pin_cal["n_above_configured_pin"] = len(above)
    pin_cal["top1_precision_above_configured_pin"] = _mean(
        [1.0 if ph["hits"][0].product_id == ph["pid"] else 0.0 for ph in above])

    # ---- (a) 只有图 ----
    a_sides = (*sides, *variants)
    rows = {s: [] for s in a_sides}
    status_a = {s: {} for s in a_sides}
    for ph in photos:
        s_prod = src(ph["pid"])
        for side in a_sides:
            res = call(side, ph["hits"], "", "a_image_only")
            status_a[side][res.status] = status_a[side].get(res.status, 0) + 1
            cards = res.products
            ids = [c.get("product_id") for c in cards]
            others = [c for c in cards if c.get("product_id") != ph["pid"]]
            rows[side].append({
                "hit1": 1.0 if ids[:1] == [ph["pid"]] else 0.0,
                "hit3": 1.0 if ph["pid"] in ids[:3] else 0.0,
                "prec": (sum(1 for c in cards if c.get("category") == s_prod.get("category")) / len(cards)) if cards else None,
                "prec_sub": (sum(1 for c in cards if c.get("sub_category") == s_prod.get("sub_category")) / len(cards)) if cards else None,
                "prec_ex": (sum(1 for c in others if c.get("category") == s_prod.get("category")) / len(others)) if others else None,
                "n": len(cards),
                "text_only_cards": sum(1 for c in cards if (c.get("_retrieval") or {}).get("source") == "text"),
            })
    results["a_image_only"] = {side: {
        "n": len(rs),
        "self_hit@1": _mean([r["hit1"] for r in rs]),
        "self_hit@3": _mean([r["hit3"] for r in rs]),
        "same_category_precision": _mean([r["prec"] for r in rs]),
        "same_sub_category_precision": _mean([r["prec_sub"] for r in rs]),
        "same_category_precision_excl_self": _mean([r["prec_ex"] for r in rs]),
        "mean_cards": _mean([r["n"] for r in rs]),
        "mean_text_path_only_cards": _mean([r["text_only_cards"] for r in rs]),
        "status_counts": status_a[side],
    } for side, rs in rows.items()}

    # ---- (a') 辨认式提问 ----
    rows = {s: [] for s in sides}
    for ph in first:
        top1 = ph["hits"][0].product_id if ph["hits"] else None
        for side in sides:
            res = call(side, ph["hits"], "这是什么", "a2_identify")
            ids = [c.get("product_id") for c in res.products]
            rows[side].append({"keep": 1.0 if ids[:1] == [top1] else 0.0,
                               "hit1": 1.0 if ids[:1] == [ph["pid"]] else 0.0})
    results["a2_identify_zheshishenme"] = {side: {
        "n": len(rs), "first_card_is_clip_top1": _mean([r["keep"] for r in rs]),
        "self_hit@1": _mean([r["hit1"] for r in rs])} for side, rs in rows.items()}

    # ---- (b) SKU 规格问题 ----
    by_sub: dict[str, list[str]] = {}
    by_cat: dict[str, list[str]] = {}
    for pid, p in catalog.items():
        by_sub.setdefault(p.get("sub_category") or "", []).append(pid)
        by_cat.setdefault(p.get("category") or "", []).append(pid)
    sku_cases = []
    for ph in first:
        p = catalog[ph["pid"]]
        for fam in ("color", "size", "storage"):
            own = _sku_values_raw(p, fam)
            if not own:
                continue
            sku_cases.append((ph, fam, own[0]))
            pool = [x for pid2 in (by_sub.get(p.get("sub_category") or "", []) + by_cat.get(p.get("category") or "", []))
                    for x in _sku_values_raw(catalog[pid2], fam)]
            neg = next((x for x in dict.fromkeys(pool) if _sku_truth(p, fam, x) is False), None)
            if neg:
                sku_cases.append((ph, fam, neg))
    b_rows = {s: [] for s in sides}
    b_actions: dict[str, int] = {}
    b_examples: list[dict] = []
    for ph, fam, value in sku_cases:
        text = _sku_question(fam, value)
        truth = _sku_truth(catalog[ph["pid"]], fam, value)
        ask = S.parse_attr_ask(F.normalize_question_forms(text))
        for side in sides:
            res = call(side, ph["hits"], text, "b_sku")
            cards = res.products
            ids = [c.get("product_id") for c in cards]
            anchor_id = (res.anchor or {}).get("product_id")
            claim = (res.sku or {}).get("anchor_has") if side == "two_path" else None
            card_has = [(_sku_truth(c, fam, value) is True) for c in cards]
            action = (res.sku or {}).get("action")
            none_claim_ok = None
            if action == "none_in_category" and res.anchor:
                kind = set(F.anchor_kind(res.anchor))
                scope = [p for p in catalog.values() if kind and p.get("sub_category") in kind] or \
                        [p for p in catalog.values() if p.get("category") == res.anchor.get("category")]
                none_claim_ok = 0.0 if any(_sku_truth(p, fam, value) for p in scope
                                           if p.get("product_id") != anchor_id) else 1.0
            row = {
                "action": action,
                "filtered_all_have": ((1.0 if cards and all(card_has) else 0.0)
                                      if action in ("filtered", "filtered_catalog") else None),
                "none_claim_ok": none_claim_ok,
                "truth": truth,
                "anchor_is_self": 1.0 if anchor_id == ph["pid"] else 0.0,
                "parsed": 1.0 if ask is not None else 0.0,
                "claim_correct": (1.0 if claim == truth else 0.0) if side == "two_path" else None,
                "top1_is_self": 1.0 if ids[:1] == [ph["pid"]] else 0.0,
                "all_cards_have": (1.0 if cards and all(card_has) else 0.0),
                "card_have_rate": (sum(card_has) / len(cards)) if cards else None,
                "no_cards": 1.0 if not cards else 0.0,
            }
            b_rows[side].append(row)
            if side == "two_path":
                act = (res.sku or {}).get("action", "none")
                b_actions[act] = b_actions.get(act, 0) + 1
                if row["claim_correct"] == 0.0 and len(b_examples) < 8:
                    b_examples.append({"pid": ph["pid"], "text": text, "truth": truth, "claim": claim,
                                       "anchor": anchor_id})

    def _b_summary(rs):
        pos = [r for r in rs if r["truth"] is True]
        neg = [r for r in rs if r["truth"] is False]
        return {
            "n": len(rs), "n_truth_has": len(pos), "n_truth_lacks": len(neg),
            "question_parsed_rate": _mean([r["parsed"] for r in rs]),
            "anchor_is_self": _mean([r["anchor_is_self"] for r in rs]),
            "answer_correct(anchor_has == truth for the photographed product)": _mean([r["claim_correct"] for r in rs]),
            "answer_correct_when_anchor_is_self": _mean([r["claim_correct"] for r in rs if r["anchor_is_self"]]),
            "truth_has: first_card_is_self": _mean([r["top1_is_self"] for r in pos]),
            "truth_lacks: all_cards_have_value": _mean([r["all_cards_have"] for r in neg]),
            "truth_lacks: card_have_rate": _mean([r["card_have_rate"] for r in neg]),
            "truth_lacks: no_card_rate": _mean([r["no_cards"] for r in neg]),
            "when SKU-filtered: all cards have the value": _mean([r["filtered_all_have"] for r in rs]),
            "when 'no product in category has it': claim true": _mean([r["none_claim_ok"] for r in rs]),
        }
    results["b_sku_question"] = {side: _b_summary(rs) for side, rs in b_rows.items()}
    results["b_sku_question"]["two_path_actions"] = b_actions
    results["b_sku_question"]["two_path_wrong_answer_examples"] = b_examples

    # ---- (c) 照片当锚点的关系问法 ----
    def _brand_same(c, ref):
        return F._brand_hit(c, F._alias_terms([ref.get("brand") or ""])) if ref.get("brand") else False

    def _price_same(c, ref):
        pc, pr = _price(c), _price(ref)
        return pc is not None and pr is not None and 0.8 * pr <= pc <= 1.2 * pr

    def _pair_ok(c, ref):
        return c.get("sub_category") in set(PAIR_MAP.get(ref.get("sub_category") or "", ()))

    rel_specs = (("same_brand", "有没有同品牌的", _brand_same, lambda p: True),
                 ("same_price", "同价位的还有吗", _price_same, lambda p: True),
                 ("pair", "这个配个什么好", _pair_ok, lambda p: (p.get("sub_category") or "") in PAIR_MAP))
    c_out: dict = {}
    for name, text, ok_fn, applicable in rel_specs:
        rows = {s: [] for s in sides}
        statuses: dict[str, int] = {}
        for ph in first:
            ref = src(ph["pid"])
            if not applicable(ref):
                continue
            for side in sides:
                res = call(side, ph["hits"], text, f"c_{name}")
                if side == "two_path":
                    statuses[res.status] = statuses.get(res.status, 0) + 1
                cards = [normalize_product_price(c) for c in res.products]
                anchor = normalize_product_price(res.anchor) if res.anchor else None
                sat_self = [(c.get("product_id") != ph["pid"]) and ok_fn(c, ref) for c in cards]
                sat_anchor = [(anchor is not None and c.get("product_id") != anchor.get("product_id")
                               and ok_fn(c, anchor)) for c in cards]
                rows[side].append({
                    "all_self": (1.0 if all(sat_self) else 0.0) if cards else None,
                    "all_anchor": (1.0 if all(sat_anchor) else 0.0) if cards else None,
                    "card_self": (sum(sat_self) / len(cards)) if cards else None,
                    "nonempty": 1.0 if cards else 0.0,
                })
        c_out[name] = {side: {
            "n": len(rs),
            "relation_satisfied(all cards, vs photographed product)": _mean([r["all_self"] for r in rs]),
            "relation_satisfied(all cards, vs visual anchor)": _mean([r["all_anchor"] for r in rs]),
            "card_level_satisfied(vs photographed product)": _mean([r["card_self"] for r in rs]),
            "nonempty_rate": _mean([r["nonempty"] for r in rs]),
        } for side, rs in rows.items()}
        c_out[name]["text"] = text
        c_out[name]["two_path_status_counts"] = statuses
    results["c_photo_relations"] = c_out

    # ---- (d) 价格 / 品牌 / 否定(违约率)----
    def _cheaper_violates(c, s, anchor):
        pc, ps = _price(c), _price(s)
        return pc is None or ps is None or pc >= ps

    def _cheaper_vs_anchor(c, anchor):
        pc, pa = _price(c), (_price(anchor) if anchor else None)
        return pc is None or pa is None or pc >= pa

    d_specs = []
    d_specs.append(("cheaper", [(ph, "这个有没有便宜点的", None) for ph in first], _cheaper_violates))
    budget_cases = []
    for ph in first:
        ps = _price(src(ph["pid"]))
        if ps is not None and ps >= 10:
            budget_cases.append((ph, f"这个有没有{int(ps * 0.8)}元以内的", int(ps * 0.8)))
    d_specs.append(("budget_80pct", budget_cases, lambda c, s, n: (_price(c) is None or _price(c) > n)))
    brand_cases = []
    for ph in first:
        brand = (catalog[ph["pid"]].get("brand") or "").strip()
        tok = brand.replace("（", " ").replace("）", " ").split()[0] if brand else ""
        text = f"有没有类似的，不要{tok}的"
        if tok and F.local_negation(F.normalize_question_forms(text)).exclude_brands:
            brand_cases.append((ph, text, brand))
    d_specs.append(("exclude_own_brand", brand_cases,
                    lambda c, s, brand: F._brand_hit(c, F._alias_terms([brand]))))
    country_cases = []
    for ph in first:
        origin = product_origin(catalog[ph["pid"]])
        word = COUNTRY_WORD.get(origin or "")
        if word:
            country_cases.append((ph, f"这个有没有类似的，不要{word}的", origin))
    d_specs.append(("exclude_own_country", country_cases, lambda c, s, origin: product_origin(c) == origin))
    d_out: dict = {}
    for name, cases, violates in d_specs:
        rows = {s: [] for s in sides}
        for ph, text, ctx in cases:
            s_prod = src(ph["pid"])
            for side in sides:
                res = call(side, ph["hits"], text, f"d_{name}")
                cards = [normalize_product_price(c) for c in res.products]
                bad = [c for c in cards if violates(c, s_prod, ctx)]
                row = {"any": 1.0 if bad else 0.0, "no_cards": 1.0 if not cards else 0.0,
                       "same_cat": (sum(1 for c in cards if c.get("category") == s_prod.get("category")) / len(cards)) if cards else None}
                if name == "cheaper":
                    anchor = normalize_product_price(res.anchor) if res.anchor else None
                    row["any_vs_anchor"] = 1.0 if any(_cheaper_vs_anchor(c, anchor) for c in cards) else 0.0
                rows[side].append(row)
        d_out[name] = {side: {
            "n": len(rs),
            "violation_rate(any card)": _mean([r["any"] for r in rs]),
            **({"violation_rate_vs_visual_anchor(the enforced constraint)": _mean([r["any_vs_anchor"] for r in rs])}
               if name == "cheaper" else {}),
            "same_category_rate": _mean([r["same_cat"] for r in rs]),
            "no_card_rate": _mean([r["no_cards"] for r in rs]),
        } for side, rs in rows.items()}
    results["d_constraints"] = d_out

    # ---- (e) 延迟 ----
    lat_out = {"clip_embed_and_search_ms": {"n": len(clip_ms), "p50": _pct(clip_ms, 0.5), "p95": _pct(clip_ms, 0.95)}}
    for slice_name, per_side in latency.items():
        lat_out[slice_name] = {side: {"n": len(v), "p50_ms": _pct(v, 0.5), "p95_ms": _pct(v, 0.95),
                                      "mean_ms": _mean(v)} for side, v in per_side.items()}
    results["e_latency_ms(fusion call only, excludes CLIP)"] = lat_out

    import platform
    return {
        "label": label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "IMAGE_MIN_SIM": F.min_sim(), "IMAGE_RECALL_N": recall, "IMAGE_TOP_K": F.image_top_k(),
            "IMAGE_PIN_SIM": F.pin_sim(), "IMAGE_TEXT_PATH_K": F.text_path_k(), "RRF_K": F.RRF_K,
            "IMAGE_TEXT_PATH_PHOTO_ONLY": F.text_path_photo_only_enabled(),
            "IMAGE_PIN_PHOTO_ONLY": F.pin_photo_only_enabled(),
            "a_variants(env overrides on top of defaults)": variants,
            "note_a": "IMAGE_TEXT_PATH_PHOTO_ONLY=0 gives exactly the cascade cards for photo-only "
                      "(no text path, visual order), so it is not run separately",
            "IMAGE_SKU_ATTRS": F.sku_attrs_enabled(), "IMAGE_PHOTO_RELATIONS": F.photo_relations_enabled(),
            "n_catalog_images": len(pairs), "n_aug_per_image": n_aug, "seed": SEED,
            "augmentation": "same as image_text_eval (crop/rotate/color jitter/downscale/JPEG)",
            "fx": "USD→CNY 7.10 fixed", "store": _store_summary(),
            "retrieval_cache": "off (RAG_RETRIEVAL_CACHE=0)",
            "llm_calls": _BLOCKED_NET_CALLS["n"], "network_calls_blocked": _BLOCKED_NET_CALLS["n"],
            "two_path_errors": len(errors),
            "machine": f"{platform.system()} {platform.machine()} python {platform.python_version()}",
            "torch_device": _torch_device(),
            "cases_a2_b_c_d_use": "augmentation #0 of each image",
        },
        "pin_calibration": pin_cal,
        "results": results,
        "errors": errors[:20],
        "elapsed_s": round(time.perf_counter() - t_start, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", default="local")
    ap.add_argument("--n-aug", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None, help="只取前 N 张目录图(调试用)")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--two-path", action="store_true",
                    help="CASCADE vs TWO-PATH 对比,写 docs/bench/image_two_path_eval_<label>.json")
    ap.add_argument("--force-cpu", action="store_true",
                    help="模型一律跑在 CPU 上(屏蔽 Apple MPS),延迟更接近线上纯 CPU 的 VM")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.force_cpu:
        import torch
        torch.backends.mps.is_available = lambda: False  # type: ignore[assignment]
    if args.two_path:
        report = run_two_path(args.label, n_aug=args.n_aug, limit=args.limit)
        out = Path(args.out) if args.out else REPO_ROOT / "docs" / "bench" / f"image_two_path_eval_{args.label}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"config": report["config"], "pin_calibration": report["pin_calibration"],
                          "results": report["results"]}, ensure_ascii=False, indent=2))
        print(f"wrote {out}")
        return 0
    report = run(args.label, n_aug=args.n_aug, calibrate=args.calibrate, limit=args.limit)
    out = Path(args.out) if args.out else REPO_ROOT / "docs" / "bench" / f"image_text_eval_{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"calibration": report["calibration"], "results": {
        k: {s: v for s, v in r.items() if s in ("old", "new")} for k, r in report["results"].items()}},
        ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
