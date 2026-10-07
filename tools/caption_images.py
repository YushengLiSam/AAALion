"""离线给商品图写"只描述看得见的东西"的中文描述(做法 A:图转文,写进文本索引)。

为什么要这一步:CLIP 图片向量只能回答"长得像不像",文本索引里又没有任何图片信息,
所以"红色瓶身那款""透明包装的"这类按外观描述的文字问题找不到商品;只发照片时,
文本那一路也没有文字可查。离线一次性写好描述,查询时就不用再调视觉模型。

做法:本地跑 Qwen3-VL-2B-Instruct(Apache-2.0,transformers + Apple MPS,不调任何付费接口),
贪心解码,输出 JSON。提示词刻意不给商品 JSON 里的品牌/价格,也要求模型别猜品牌、价格、
功效、产地 —— 那些以商品数据为准;描述只补文本里没有的视觉属性。

产物:data/derived/image_captions.jsonl(一行一个商品;带模型、修订号、提示词版本,可追溯)。
rag/ingest/chunk.py 读它生成 chunk_type=image_caption 的文本块。

用法::

    .venv/bin/python tools/caption_images.py                 # 全部 145 张,已有的跳过
    .venv/bin/python tools/caption_images.py --limit 3 --out /tmp/cap.jsonl   # 试跑
    .venv/bin/python tools/caption_images.py --force         # 重写全部
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = REPO_ROOT / "data" / "seed"
DEFAULT_OUT = REPO_ROOT / "data" / "derived" / "image_captions.jsonl"
MODEL_ID = "Qwen/Qwen3-VL-2B-Instruct"
PROMPT_VERSION = "v1-2026-10-07"
MAX_EDGE = 768  # 2B 模型看商品主图,768px 足够;越大越慢

PROMPT = (
    "你在为电商商品图建立检索索引。只描述这张图里**看得见**的东西。"
    "不要猜品牌、价格、功效、产地、适用人群,也不要写图里看不到的卖点。\n"
    "只输出一个 JSON 对象,字段如下:\n"
    "appearance:一句话外观描述,不超过 50 字(品类外观、形状、包装形态、摆放方式);\n"
    "colors:图中这件商品的主要颜色,数组,最多 3 个,只写这一款在图里显示的颜色;\n"
    "materials:看得出的材质或质感,数组,看不出就给空数组;\n"
    "style:款式或风格关键词,数组,最多 4 个;\n"
    "visible_text:图中能看清的文字,原样抄录,数组,最多 6 条,看不清就给空数组。\n"
    "只输出 JSON,不要任何解释。"
)


def _iter_images() -> list[tuple[str, Path]]:
    """(product_id, 图片路径)。与图片索引用同一套文件名 → 商品 ID 规则
    (rag.ingest.embed_image.iter_product_images:p_xxx_live.jpg → p_xxx),否则带 _live 后缀的
    图片会和商品对不上。"""
    sys.path.insert(0, str(REPO_ROOT))
    from rag.ingest.embed_image import iter_product_images

    return sorted(iter_product_images(SEED), key=lambda t: str(t[1]))


def _normalize_id(row: dict) -> str:
    """旧版本用文件名 stem 当 ID(带 _live);读回时按图片路径换算成真正的商品 ID。"""
    sys.path.insert(0, str(REPO_ROOT))
    from rag.ingest.embed_image import _product_id

    img = row.get("image")
    return (_product_id(REPO_ROOT / img) if img else None) or row["product_id"]


def _load_model():
    import torch
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "mps" else torch.float32
    model = Qwen3VLForConditionalGeneration.from_pretrained(MODEL_ID, dtype=dtype)
    model.to(device).eval()
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    return model, processor, device


def _revision() -> str:
    try:
        from huggingface_hub import HfApi

        return HfApi().model_info(MODEL_ID).sha or ""
    except Exception:
        return ""


def _parse_json(text: str) -> dict | None:
    t = re.sub(r"^```\w*\s*|```\s*$", "", (text or "").strip())
    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    def _list(v, n):
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in (v or []) if str(x).strip()][:n]

    return {
        "appearance": str(obj.get("appearance") or "").strip()[:80],
        "colors": _list(obj.get("colors"), 3),
        "materials": _list(obj.get("materials"), 4),
        "style": _list(obj.get("style"), 4),
        "visible_text": _list(obj.get("visible_text"), 6),
    }


def _salvage(text: str) -> dict | None:
    """输出被 max_new_tokens 截断时(图里文字很多,模型逐条抄 visible_text 抄到上限,
    例如配料表、表盘刻度),逐字段抢救已经写完的部分;没有 appearance 就放弃。"""
    m = re.search(r'"appearance"\s*:\s*"([^"]*)"', text or "")
    if not m:
        return None

    def _arr(key: str, n: int) -> list[str]:
        mm = re.search(r'"%s"\s*:\s*\[(.*?)(?:\]|$)' % key, text, flags=re.S)
        vals = re.findall(r'"([^"\n]+)"', mm.group(1)) if mm else []
        return list(dict.fromkeys(v.strip() for v in vals if v.strip()))[:n]

    return {"appearance": m.group(1).strip()[:80], "colors": _arr("colors", 3), "materials": _arr("materials", 4),
            "style": _arr("style", 4), "visible_text": _arr("visible_text", 6)}


def _caption(model, processor, device, path: Path) -> tuple[dict | None, str]:
    import torch
    from PIL import Image

    img = Image.open(path).convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_EDGE:
        s = MAX_EDGE / max(w, h)
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    messages = [{"role": "user", "content": [{"type": "image", "image": img},
                                             {"type": "text", "text": PROMPT}]}]
    inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                           return_dict=True, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=256, do_sample=False)
    raw = processor.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]
    return _parse_json(raw) or _salvage(raw), raw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="ignore existing rows and recaption everything")
    args = ap.parse_args()

    images = _iter_images()
    if args.limit:
        images = images[: args.limit]
    done: dict[str, dict] = {}
    if args.out.exists() and not args.force:
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["product_id"] = _normalize_id(row)
                if row.get("caption"):
                    done[row["product_id"]] = row
    todo = [(pid, p) for pid, p in images if pid not in done]
    print(f"[caption] {len(images)} images, {len(done)} already captioned, {len(todo)} to do", file=sys.stderr)
    if todo:
        t0 = time.time()
        model, processor, device = _load_model()
        rev = _revision()
        print(f"[caption] loaded {MODEL_ID}@{rev[:12]} on {device} in {time.time() - t0:.1f}s", file=sys.stderr)
        for i, (pid, path) in enumerate(todo, 1):
            t = time.time()
            cap, raw = _caption(model, processor, device, path)
            if cap is None:  # 一次重试:2B 模型偶尔会多写解释
                cap, raw = _caption(model, processor, device, path)
            done[pid] = {
                "product_id": pid,
                "image": str(path.relative_to(REPO_ROOT)),
                "caption": cap,
                "raw": None if cap else raw[:500],
                "model": MODEL_ID,
                "model_revision": rev,
                "prompt_version": PROMPT_VERSION,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            print(f"[caption] {i}/{len(todo)} {pid} {time.time() - t:.1f}s "
                  f"{'OK' if cap else 'PARSE_FAIL'} {(cap or {}).get('appearance', '')[:40]}", file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows = [done[pid] for pid, _ in _iter_images() if pid in done]
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    ok = sum(1 for r in rows if r.get("caption"))
    print(f"[caption] wrote {len(rows)} rows ({ok} parsed) -> {args.out}", file=sys.stderr)
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
