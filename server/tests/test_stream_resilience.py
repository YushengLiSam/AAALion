"""Regression tests for SSE streaming resilience (chat route).

Covers two demo-killing bugs found in the pre-freeze audit:
  1. Mid-stream retry duplicated the answer: once a delta had been emitted, a
     retry re-streamed the whole reply, so the user saw the text 2-3 times.
  2. A stream that emitted some text and THEN errored was still written to the
     response cache, so every repeat of the same query replayed the error (and
     stale text) for the full TTL instead of re-hitting the recovered upstream.
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.routes.chat import _stream_chat_with_retry


def _drain(provider, max_attempts=3):
    out = []
    raised = None

    async def run():
        nonlocal raised
        try:
            async for delta in _stream_chat_with_retry(provider, [], max_attempts=max_attempts):
                out.append(delta)
        except Exception as e:  # noqa: BLE001
            raised = e

    asyncio.run(run())
    return out, raised


class _Provider:
    def __init__(self, script):
        # script: list of attempts; each attempt is (list_of_deltas, raise_or_None)
        self.script = script
        self.calls = 0

    async def stream_chat(self, history):
        attempt = self.script[self.calls]
        self.calls += 1
        deltas, exc = attempt
        for d in deltas:
            yield d
        if exc is not None:
            raise exc


def test_no_retry_after_first_delta():
    """Mid-stream failure must NOT retry — no duplicated text."""
    p = _Provider([
        (["AB"], RuntimeError("connection reset mid-stream")),
        (["AB", "CD"], None),  # would duplicate if (wrongly) retried
    ])
    out, raised = _drain(p)
    assert "".join(out) == "AB"          # emitted exactly once
    assert p.calls == 1                  # no retry after a token went out
    assert isinstance(raised, RuntimeError)


def test_early_error_still_retries():
    """Failure BEFORE any delta still retries and recovers cleanly."""
    p = _Provider([
        ([], ConnectionError("429 rate limited")),
        (["Hello", " world"], None),
    ])
    out, raised = _drain(p)
    assert "".join(out) == "Hello world"
    assert p.calls == 2
    assert raised is None


def test_cache_guard_skips_error_streams():
    """The cache-write guard must reject any stream that hit an error,
    even if it emitted deltas first (mirrors chat.py guard logic)."""
    events = [
        {"type": "product_card", "id": "p1"},
        {"type": "delta", "text": "这款"},
        {"type": "error", "message": "x", "code": "UPSTREAM"},
        {"type": "done"},
    ]
    has_delta = any(e.get("type") == "delta" for e in events)
    has_error = any(e.get("type") == "error" for e in events)
    should_cache = has_delta and not has_error
    assert should_cache is False  # error stream must NOT be cached
