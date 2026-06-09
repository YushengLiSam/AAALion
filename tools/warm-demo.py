#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-warm the demo backend before a live demo / defense.

Models are prewarmed at startup (RAG_PREWARM), but the *retrieval cache*
(rag_client._heavy_retrieve memo, TTL = RAG_RETRIEVAL_CACHE_TTL, default 300s)
only holds queries that have actually been asked — so the FIRST time each demo
query is asked it pays the full cold cost (~25-40s for English on the CPU VM).

Run this right before the demo to make every scripted query land warm (~0.3s):

    python tools/warm-demo.py --base https://<tunnel>.trycloudflare.com
    # behind a proxy (e.g. from Windows/Clash):
    python tools/warm-demo.py --base https://... --proxy http://127.0.0.1:7897

Tip: also bump the cache TTL for the demo window so warmed queries don't expire
mid-session, e.g. in the systemd unit / .env on the VM:
    RAG_RETRIEVAL_CACHE_TTL=3600
"""
import argparse
import json
import ssl
import time
import urllib.request

# Curated to cover the demo scenarios; English queries first since they are the
# slowest cold and benefit most from warming.
DEMO_QUERIES = [
    # English (slow cold — most important to warm)
    ("en", "running shoes under 1000"),
    ("en", "luxury Japanese skincare"),
    ("en", "noise-cancelling headphones under 3000"),
    ("en", "moisturizer for oily skin"),
    ("en", "a birthday gift for my girlfriend"),
    # Chinese (the headline scripted scenarios)
    ("zh", "推荐一款适合油皮的洗面奶"),
    ("zh", "150元以内的口红"),
    ("zh", "不要苹果的耳机"),
    ("zh", "Sony WH-1000XM5 和 Bose QC45 哪个好"),
    ("zh", "2000元以下的智能手表"),
    ("zh", "推荐抗初老精华,不要兰蔻"),
    ("zh", "推荐 iPhone"),
    ("zh", "送女友的生日礼物,预算 500 左右"),
]


def make_opener(proxy):
    handlers = [urllib.request.HTTPSHandler(context=ssl._create_unverified_context())]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def ask(opener, base, lang, query, timeout):
    body = json.dumps({"messages": [{"role": "user", "content": query}],
                       "language": lang}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/stream", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    n_cards = 0
    with opener.open(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    for blk in text.split("\n\n"):
        blk = blk.strip()
        if not blk.startswith("data:"):
            continue
        try:
            ev = json.loads(blk[5:].strip())
        except Exception:
            continue
        if first is None and ev.get("type") in ("delta", "product_card", "clarify"):
            first = time.time() - t0
        if ev.get("type") == "product_card":
            n_cards += 1
    return (first or (time.time() - t0)), time.time() - t0, n_cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="backend base URL")
    ap.add_argument("--proxy", default=None, help="optional HTTP proxy, e.g. http://127.0.0.1:7897")
    ap.add_argument("--rounds", type=int, default=2, help="passes over the query set (2 = prove cache hit)")
    ap.add_argument("--timeout", type=float, default=90)
    args = ap.parse_args()

    opener = make_opener(args.proxy)
    print(f"Warming {args.base}  ({len(DEMO_QUERIES)} queries x {args.rounds} rounds)\n")
    for rnd in range(1, args.rounds + 1):
        print(f"--- round {rnd} ---")
        worst = 0.0
        for lang, q in DEMO_QUERIES:
            try:
                first, total, cards = ask(opener, args.base, lang, q, args.timeout)
                worst = max(worst, first)
                flag = "🔥" if first > 8 else "✅"
                print(f"  {flag} [{lang}] first={first:5.1f}s total={total:5.1f}s cards={cards}  {q[:32]}")
            except Exception as exc:
                print(f"  ❌ [{lang}] {q[:32]} -> {exc!r}")
        print(f"  round {rnd} worst first-token: {worst:.1f}s\n")
    print("Done. If round 2 first-tokens are all low (<2s), the cache is warm.")


if __name__ == "__main__":
    main()
