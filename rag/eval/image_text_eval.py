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


def _hermetic_env() -> None:
    for key in ("TOKENROUTER_API_KEY", "DOUBAO_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)
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
            "llm_calls": 0,
            "cases_b_c_use": "augmentation #0 of each image",
            "old_path": "raw CLIP top-3 (same top-N recall list truncated to 3), text ignored, no floor",
        },
        "calibration": calibration if calibrate else {k: calibration[k] for k in (
            "configured_floor", "positive_retention_at_configured_floor", "negative_rejection_at_configured_floor")},
        "results": results,
        "elapsed_s": round(time.perf_counter() - t_start, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", default="local")
    ap.add_argument("--n-aug", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None, help="只取前 N 张目录图(调试用)")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
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
