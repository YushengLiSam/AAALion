#!/usr/bin/env python3
"""Async stress-test for the /chat/stream SSE endpoint.

Spins up N concurrent workers, each looping a small set of representative
queries and measuring end-to-end "first-delta" + "stream completion" latency.
Prints a summary at the end: p50 / p95 / p99 latencies, success rate, throughput.

Usage:
    python tools/stress_test.py                       # default: 30 workers, 60s
    python tools/stress_test.py --workers 50 --secs 30
    BACKEND_URL=http://192.168.22.50:8000 python tools/stress_test.py

The test deliberately hits a mix of cached and uncached queries (the LRU
cache is keyed on system+messages, so repeating the same query within the
TTL gets a near-instant replay; this measures the realistic blended
performance, not a worst-case-only number).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from contextlib import suppress

import httpx

QUERIES = [
    "推荐一款适合油皮的洗面奶",
    "200元以下的蓝牙耳机有哪些",
    "推荐防晒霜不要日系品牌",
    "雅诗兰黛小棕瓶和兰蔻小黑瓶哪个更适合熬夜",
    "推荐一本科幻小说",
    "推荐一台量子计算机",
    "Sony WH-1000XM5 怎么样",
    "iPhone 15 Pro 多少钱",
    "推荐一款适合敏感肌的面霜",
    "我想买点零食",
]


async def one_request(client: httpx.AsyncClient, url: str, query: str) -> tuple[bool, float, float | None]:
    """Returns (success, total_seconds, first_delta_seconds_or_None)."""
    payload = {"messages": [{"role": "user", "content": query}]}
    t0 = time.perf_counter()
    first_delta_at: float | None = None
    try:
        async with client.stream("POST", url, json=payload, timeout=30.0) as r:
            if r.status_code != 200:
                return (False, time.perf_counter() - t0, None)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                if first_delta_at is None:
                    first_delta_at = time.perf_counter() - t0
                if '"type": "done"' in line or '"type":"done"' in line:
                    break
    except Exception:
        return (False, time.perf_counter() - t0, first_delta_at)
    return (True, time.perf_counter() - t0, first_delta_at)


async def worker(wid: int, base: str, secs: float, results: list, stop_at: float) -> None:
    url = base.rstrip("/") + "/chat/stream"
    async with httpx.AsyncClient(timeout=30.0) as client:
        idx = wid % len(QUERIES)
        while time.perf_counter() < stop_at:
            query = QUERIES[idx % len(QUERIES)]
            idx += 1
            ok, total, first = await one_request(client, url, query)
            results.append((ok, total, first))


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=30, help="concurrent workers")
    ap.add_argument("--secs", type=float, default=60.0, help="duration in seconds")
    ap.add_argument("--base", default=os.environ.get("BACKEND_URL", "http://localhost:8000"))
    args = ap.parse_args()

    results: list[tuple[bool, float, float | None]] = []
    stop_at = time.perf_counter() + args.secs
    print(f"Stress test: {args.workers} workers × {args.secs}s against {args.base}/chat/stream")
    t0 = time.perf_counter()
    await asyncio.gather(*(worker(i, args.base, args.secs, results, stop_at) for i in range(args.workers)))
    elapsed = time.perf_counter() - t0

    total = len(results)
    ok_count = sum(1 for ok, _, _ in results if ok)
    if total == 0:
        print("no responses captured")
        return
    success_rate = ok_count / total
    rps = total / elapsed
    ok_totals = [t for ok, t, _ in results if ok]
    ok_firsts = [f for ok, _, f in results if ok and f is not None]
    print()
    print(f"Duration       : {elapsed:.1f}s")
    print(f"Total reqs     : {total}  (success {ok_count} = {success_rate:.1%})")
    print(f"Throughput     : {rps:.1f} req/s")
    print(f"Total latency  : p50={pct(ok_totals,50)*1000:.0f}ms  p95={pct(ok_totals,95)*1000:.0f}ms  p99={pct(ok_totals,99)*1000:.0f}ms  mean={statistics.mean(ok_totals)*1000:.0f}ms")
    if ok_firsts:
        print(f"First-delta    : p50={pct(ok_firsts,50)*1000:.0f}ms  p95={pct(ok_firsts,95)*1000:.0f}ms  p99={pct(ok_firsts,99)*1000:.0f}ms  mean={statistics.mean(ok_firsts)*1000:.0f}ms")


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
