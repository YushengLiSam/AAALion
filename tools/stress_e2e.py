"""End-to-end stress test of /chat/stream across the failure modes we
discovered tonight + general load.

Run after R8.F.7 to verify nothing else is on fire:

  python tools/stress_e2e.py --base http://localhost:8000

Two phases:

  Phase 1 — CORRECTNESS suite (sequential, single-shot per case):
    Every case is a (query, must_contain_in_titles, must_not_contain)
    triple. The runner sends a /chat/stream POST, harvests the
    product_card SSE events, and asserts the title set follows the
    rule. Failure prints offending product titles for diagnosis.

  Phase 2 — LOAD suite (concurrent, throughput + error rate):
    Spam N parallel /chat/stream calls with a fixed prompt for ~30 s.
    Reports success rate, p50 / p95 first-delta latency, and any
    upstream 5xx / SSE parse errors.

The correctness suite includes:
  * the original "Give me an iPhone" → iPad bug (R8.F.4)
  * the "iPhone13" → iPad 13英寸 bug (R8.F.6)
  * the multi-turn skincare → "iPhone 12" pollution (R8.F.7)
  * negation regressions (Tujie R7+ stuff)
  * pure Chinese baseline (should match eval recall@5 = 0.964+)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# SSE harvesting
# ---------------------------------------------------------------------------

@dataclass
class ChatResult:
    product_titles: list[str]
    delta_text: str
    first_delta_ms: float | None
    total_ms: float
    error: str | None = None


def _chat(base_url: str, messages: list[dict], timeout: float = 60.0) -> ChatResult:
    body = json.dumps({"messages": messages}).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/stream",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    t0 = time.perf_counter()
    titles: list[str] = []
    delta_text = ""
    first_delta_ms: float | None = None
    error: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buffer = ""
            for chunk in resp:
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    for line in event.splitlines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            ev = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if ev.get("type") == "product_card":
                            p = ev.get("product") or {}
                            titles.append(p.get("title", ""))
                        elif ev.get("type") == "delta":
                            if first_delta_ms is None:
                                first_delta_ms = (time.perf_counter() - t0) * 1000
                            delta_text += ev.get("text", "")
                        elif ev.get("type") == "error":
                            error = ev.get("message", "<unknown error event>")
    except urllib.error.HTTPError as e:
        error = f"HTTP {e.code}: {e.reason}"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    return ChatResult(
        product_titles=titles,
        delta_text=delta_text,
        first_delta_ms=first_delta_ms,
        total_ms=(time.perf_counter() - t0) * 1000,
        error=error,
    )


# ---------------------------------------------------------------------------
# Phase 1 — correctness cases
# ---------------------------------------------------------------------------

@dataclass
class CorrectnessCase:
    name: str
    # Either a single user message string, or a multi-turn list of {role, content} dicts.
    messages: list[dict]
    must_contain_any: list[str]  # at least one of these tokens must appear in a returned title
    must_not_contain: list[str]  # NO returned title may contain any of these


def _build_correctness_cases() -> list[CorrectnessCase]:
    return [
        CorrectnessCase(
            name="R8.F.4: 'Give me an iPhone' should NOT return iPad",
            messages=[{"role": "user", "content": "Give me an iPhone"}],
            must_contain_any=["iPhone"],
            must_not_contain=["iPad"],
        ),
        CorrectnessCase(
            name="R8.F.6a: 'iPhone13' should return iPhone, not iPad 13英寸",
            messages=[{"role": "user", "content": "iPhone13"}],
            must_contain_any=["iPhone"],
            must_not_contain=["iPad", "MacBook"],
        ),
        CorrectnessCase(
            name="R8.F.6b: 'iPhone 13' (with space) same expectation",
            messages=[{"role": "user", "content": "iPhone 13"}],
            must_contain_any=["iPhone"],
            must_not_contain=["iPad", "MacBook"],
        ),
        CorrectnessCase(
            name="R8.F.7: topic switch skincare -> 'iPhone 12' must return iPhones, not skincare",
            messages=[
                {"role": "user", "content": "推荐适合敏感肌的洁面"},
                {"role": "assistant", "content": "为您推荐薇诺娜舒敏洁面..."},
                {"role": "user", "content": "I want a iPhone 12"},
            ],
            must_contain_any=["iPhone"],
            must_not_contain=["科颜氏", "花西子", "珀莱雅", "资生堂"],
        ),
        CorrectnessCase(
            name="topic switch CN: 美妆 -> 推荐 iPad",
            messages=[
                {"role": "user", "content": "推荐一款日常洁面"},
                {"role": "assistant", "content": "为您推荐..."},
                {"role": "user", "content": "推荐 iPad"},
            ],
            must_contain_any=["iPad"],
            must_not_contain=["洁面", "面霜", "精华"],
        ),
        CorrectnessCase(
            name="Chinese baseline: '推荐一款适合油皮的洗面奶' should return cleansers",
            messages=[{"role": "user", "content": "推荐一款适合油皮的洗面奶"}],
            must_contain_any=["洁面", "洗面", "净颜", "卸妆", "氨基酸"],
            must_not_contain=["iPhone", "iPad", "MacBook"],
        ),
        CorrectnessCase(
            name="Negation: '推荐面霜 不要日系' should NOT include Japanese skincare brands",
            messages=[{"role": "user", "content": "推荐面霜,不要日系"}],
            must_contain_any=["面霜", "霜"],
            must_not_contain=["资生堂", "SK-II", "Tatcha", "FANCL", "芳珂"],
        ),
        CorrectnessCase(
            name="Mixed CN+EN brand: '我想买 OPPO 手机'",
            messages=[{"role": "user", "content": "我想买 OPPO 手机"}],
            must_contain_any=["OPPO"],
            must_not_contain=["iPhone", "iPad"],
        ),
        CorrectnessCase(
            name="Multi-turn negation persistence: turn 1 不要日系, turn 2 再便宜点",
            messages=[
                {"role": "user", "content": "推荐防晒,不要日系"},
                {"role": "assistant", "content": "为您推荐..."},
                {"role": "user", "content": "再便宜点的"},
            ],
            must_contain_any=[],  # only forbidden assertion matters
            must_not_contain=["安热沙", "资生堂", "SK-II"],
        ),
        CorrectnessCase(
            name="Empty-result honesty: 'iPhone 13' (we don't carry it) — LLM should not hallucinate",
            messages=[{"role": "user", "content": "iPhone 13 多少钱"}],
            must_contain_any=["iPhone"],  # the anchor filter returns iPhone 17 Pro family
            must_not_contain=["iPad"],
        ),
    ]


def _passes(case: CorrectnessCase, result: ChatResult) -> tuple[bool, str]:
    if result.error:
        return False, f"transport/server error: {result.error}"
    titles = result.product_titles
    if not titles:
        return False, "no product_card events received"

    if case.must_contain_any:
        if not any(any(token in t for token in case.must_contain_any) for t in titles):
            return False, (
                f"none of {case.must_contain_any} appear in titles: "
                + " | ".join(titles[:5])
            )
    bad = [t for t in titles if any(bad in t for bad in case.must_not_contain)]
    if bad:
        return False, (
            f"forbidden tokens hit. {case.must_not_contain} found in: "
            + " | ".join(bad[:3])
        )
    return True, "ok"


def run_correctness(base_url: str) -> tuple[int, int]:
    cases = _build_correctness_cases()
    passed = 0
    failed = 0
    print(f"\n=== Phase 1: correctness ({len(cases)} cases) ===")
    for i, case in enumerate(cases, 1):
        result = _chat(base_url, case.messages, timeout=60)
        ok, why = _passes(case, result)
        status = "✓" if ok else "✗"
        latency = f"{result.first_delta_ms:>5.0f}ms" if result.first_delta_ms else "  N/A"
        print(f"  [{i:>2}/{len(cases)}] {status} {latency}  {case.name}")
        if ok:
            passed += 1
        else:
            failed += 1
            print(f"           ↳ {why}")
            print(f"           ↳ titles returned: {' | '.join(result.product_titles[:5])}")
    print(f"\n  Phase 1 result: {passed} passed / {failed} failed")
    return passed, failed


# ---------------------------------------------------------------------------
# Phase 2 — load test
# ---------------------------------------------------------------------------

def _spam_once(base_url: str, prompt: str) -> tuple[float | None, float, str | None]:
    r = _chat(base_url, [{"role": "user", "content": prompt}], timeout=90)
    return r.first_delta_ms, r.total_ms, r.error


def run_load(base_url: str, workers: int, duration_sec: int) -> None:
    prompts = [
        "推荐一款日常洁面",
        "200元以下的蓝牙耳机有哪些",
        "Give me an iPhone",
        "推荐 iPad Pro",
        "适合敏感肌的精华推荐",
    ]
    print(f"\n=== Phase 2: load ({workers} workers, {duration_sec}s) ===")
    first_deltas: list[float] = []
    totals: list[float] = []
    errors: list[str] = []
    completed = 0
    started_at = time.perf_counter()
    deadline = started_at + duration_sec

    def _one(p: str) -> tuple[float | None, float, str | None]:
        return _spam_once(base_url, p)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures: list[cf.Future] = []
        next_prompt_idx = 0
        while time.perf_counter() < deadline:
            while sum(1 for f in futures if not f.done()) < workers and time.perf_counter() < deadline:
                p = prompts[next_prompt_idx % len(prompts)]
                next_prompt_idx += 1
                futures.append(ex.submit(_one, p))
            done = [f for f in futures if f.done()]
            for f in done:
                fd, tt, err = f.result()
                if fd is not None:
                    first_deltas.append(fd)
                totals.append(tt)
                if err:
                    errors.append(err)
                completed += 1
                futures.remove(f)
            time.sleep(0.05)
        # Drain
        for f in cf.as_completed(futures, timeout=120):
            fd, tt, err = f.result()
            if fd is not None:
                first_deltas.append(fd)
            totals.append(tt)
            if err:
                errors.append(err)
            completed += 1

    elapsed = time.perf_counter() - started_at
    rps = completed / elapsed if elapsed > 0 else 0
    print(f"  total requests:  {completed}")
    print(f"  elapsed:         {elapsed:.1f}s   ({rps:.2f} req/s)")
    print(f"  success:         {completed - len(errors)} / {completed}  ({(1 - len(errors)/max(completed,1))*100:.1f}%)")
    if first_deltas:
        first_deltas.sort()
        print(f"  first-delta ms:  p50={statistics.median(first_deltas):.0f}  "
              f"p95={first_deltas[int(len(first_deltas)*0.95)]:.0f}  "
              f"mean={statistics.mean(first_deltas):.0f}")
    if totals:
        totals.sort()
        print(f"  total ms:        p50={statistics.median(totals):.0f}  "
              f"p95={totals[int(len(totals)*0.95)]:.0f}  "
              f"mean={statistics.mean(totals):.0f}")
    if errors:
        print(f"  errors (first 5):")
        for e in errors[:5]:
            print(f"    - {e}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--skip-load", action="store_true", help="run only correctness phase")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--secs", type=int, default=20)
    args = ap.parse_args()

    # Probe.
    try:
        with urllib.request.urlopen(f"{args.base}/ready", timeout=5) as resp:
            ready = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"backend not reachable at {args.base}: {e}", file=sys.stderr)
        return 2
    if (ready.get("status") or "") != "ready":
        print(f"backend not ready: {ready}", file=sys.stderr)
        return 2

    p_pass, p_fail = run_correctness(args.base)
    if not args.skip_load:
        run_load(args.base, args.workers, args.secs)

    return 0 if p_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
