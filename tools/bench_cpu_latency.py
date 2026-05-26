"""Measure the three heavy models' inference latency on CPU vs MPS (Apple
Silicon GPU). The result drives VM sizing — if CPU is within budget, deploy
on a c-series instance; otherwise need GPU.

Models under test:
  * BGE-small-zh-v1.5     — query embedder, 1 query / request (hot path)
  * bge-reranker-base     — cross-encoder, 30 pairs / request (hot path)
  * OpenCLIP ViT-B/32     — image encoder, 1 image / multimodal request

Each is timed 5 warmup + 20 measured. Reports mean / p50 / p95.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("CHROMA_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _stats(samples_ms: list[float]) -> dict:
    s = sorted(samples_ms)
    return {
        "mean": round(mean(s), 1),
        "p50": round(median(s), 1),
        "p95": round(s[int(len(s) * 0.95)], 1),
        "min": round(min(s), 1),
        "max": round(max(s), 1),
    }


def bench_embedder(device: str, n_warmup: int = 5, n_measure: int = 20) -> dict:
    from sentence_transformers import SentenceTransformer

    print(f"  loading BGE-small-zh on {device}...", end=" ", flush=True)
    t0 = time.perf_counter()
    model = SentenceTransformer("BAAI/bge-small-zh-v1.5", device=device)
    print(f"loaded in {(time.perf_counter() - t0):.1f}s")

    query = "推荐适合敏感肌的面霜,预算 300 元"
    for _ in range(n_warmup):
        model.encode(query, convert_to_numpy=True)

    samples = []
    for _ in range(n_measure):
        t0 = time.perf_counter()
        model.encode(query, convert_to_numpy=True)
        samples.append((time.perf_counter() - t0) * 1000)
    return _stats(samples)


def bench_reranker(device: str, n_pairs: int = 30, n_warmup: int = 5, n_measure: int = 20) -> dict:
    from sentence_transformers import CrossEncoder

    print(f"  loading bge-reranker-base on {device}...", end=" ", flush=True)
    t0 = time.perf_counter()
    model = CrossEncoder("BAAI/bge-reranker-base", max_length=256, device=device)
    print(f"loaded in {(time.perf_counter() - t0):.1f}s")

    query = "推荐适合敏感肌的面霜,预算 300 元"
    candidates = [
        f"雅诗兰黛小棕瓶面霜,适合敏感干性肌肤,温和不刺激,精华成分。价格 ¥{300 + i*10}"
        for i in range(n_pairs)
    ]
    pairs = [[query, c] for c in candidates]

    for _ in range(n_warmup):
        model.predict(pairs)

    samples = []
    for _ in range(n_measure):
        t0 = time.perf_counter()
        model.predict(pairs)
        samples.append((time.perf_counter() - t0) * 1000)
    return _stats(samples)


def bench_clip_image(device: str, n_warmup: int = 5, n_measure: int = 20) -> dict:
    import torch
    import open_clip
    from PIL import Image
    import io

    print(f"  loading OpenCLIP ViT-B-32 on {device}...", end=" ", flush=True)
    t0 = time.perf_counter()
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model = model.to(device).eval()
    print(f"loaded in {(time.perf_counter() - t0):.1f}s")

    # Realistic input: a real product image from the catalog
    img_path = REPO_ROOT / "data" / "seed" / "1_美妆护肤" / "images" / "p_1_intl_01.jpg"
    img = Image.open(img_path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(device)

    with torch.no_grad():
        for _ in range(n_warmup):
            feat = model.encode_image(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)

        samples = []
        for _ in range(n_measure):
            t0 = time.perf_counter()
            feat = model.encode_image(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)
            if device != "cpu":
                # Force device sync so we time the actual GPU work, not just kernel launch.
                if device == "cuda":
                    torch.cuda.synchronize()
                elif device == "mps":
                    torch.mps.synchronize()
            samples.append((time.perf_counter() - t0) * 1000)
    return _stats(samples)


def _format_table(rows: list[tuple[str, dict, dict, str]]) -> str:
    """rows = [(name, cpu_stats, accel_stats, notes), ...]"""
    out = []
    out.append("\n" + "=" * 100)
    out.append(f"{'Component':<30} {'CPU mean':>12} {'CPU p95':>10} {'MPS mean':>12} {'MPS p95':>10} {'CPU/MPS':>8}  Notes")
    out.append("-" * 100)
    for name, cpu, mps, notes in rows:
        ratio = cpu["mean"] / mps["mean"] if mps["mean"] else float("inf")
        out.append(
            f"{name:<30} {cpu['mean']:>10.1f}ms {cpu['p95']:>8.1f}ms "
            f"{mps['mean']:>10.1f}ms {mps['p95']:>8.1f}ms "
            f"{ratio:>7.1f}×  {notes}"
        )
    out.append("=" * 100)
    return "\n".join(out)


def main() -> int:
    import torch

    has_mps = torch.backends.mps.is_available()
    has_cuda = torch.cuda.is_available()
    accel = "cuda" if has_cuda else ("mps" if has_mps else "cpu")
    print(f"Accelerator: {accel} (cuda={has_cuda}, mps={has_mps})")
    print(f"Torch threads: {torch.get_num_threads()}")
    print()

    rows = []

    print("[1/3] BGE-small-zh-v1.5 (query embedder, 1 query)")
    cpu = bench_embedder("cpu")
    print(f"    CPU: {cpu}")
    if accel != "cpu":
        acc = bench_embedder(accel)
        print(f"    {accel.upper()}: {acc}")
    else:
        acc = cpu
    rows.append(("BGE embed (1 query)", cpu, acc, "every text request"))

    print("\n[2/3] bge-reranker-base (cross-encoder, 30 pairs)")
    cpu = bench_reranker("cpu", n_pairs=30)
    print(f"    CPU: {cpu}")
    if accel != "cpu":
        acc = bench_reranker(accel, n_pairs=30)
        print(f"    {accel.upper()}: {acc}")
    else:
        acc = cpu
    rows.append(("Reranker (30 pairs)", cpu, acc, "every text request"))

    print("\n[3/3] OpenCLIP ViT-B/32 (image encoder, 1 image)")
    cpu = bench_clip_image("cpu")
    print(f"    CPU: {cpu}")
    if accel != "cpu":
        acc = bench_clip_image(accel)
        print(f"    {accel.upper()}: {acc}")
    else:
        acc = cpu
    rows.append(("CLIP image (1 img)", cpu, acc, "only multimodal requests"))

    print(_format_table(rows))

    # Compose end-to-end summary for a typical text request:
    cpu_total_text = rows[0][1]["mean"] + rows[1][1]["mean"]
    acc_total_text = rows[0][2]["mean"] + rows[1][2]["mean"]
    print(f"\nText request hot path (BGE + reranker), per request:")
    print(f"  CPU:  {cpu_total_text:.0f} ms       MPS: {acc_total_text:.0f} ms")
    cpu_total_img = rows[2][1]["mean"]
    acc_total_img = rows[2][2]["mean"]
    print(f"Image request hot path (CLIP encode), per request:")
    print(f"  CPU:  {cpu_total_img:.0f} ms       MPS: {acc_total_img:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
