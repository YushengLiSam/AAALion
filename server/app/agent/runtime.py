"""chat.py 与智能体之间的薄胶水层:开关、并发闸、影子运行、trace 落盘。

环境变量:
  AGENT_PATH             off(默认,零行为变化)| shadow(后台跑、只记 trace)| on
  AGENT_MAX_CONCURRENCY  同时在跑的智能体数上限(默认 2);满了直接走快路 / 跳过影子
  AGENT_TIMEOUT_S        智能体路径总时长上限(默认 8 秒),超时回退快路
  AGENT_SHADOW_LOG       影子 trace 路径(默认 data/.agent/shadow.jsonl)

本模块的任何函数都不抛异常:智能体出任何问题,调用方都按"没有结果"处理、走快路。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("agent")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_sem: asyncio.Semaphore | None = None
_sem_size: int | None = None
_shadow_tasks: set[asyncio.Task] = set()
_log_lock = threading.Lock()


def agent_mode() -> str:
    mode = (os.getenv("AGENT_PATH") or "off").strip().lower()
    return mode if mode in ("off", "shadow", "on") else "off"


def _semaphore() -> asyncio.Semaphore:
    global _sem, _sem_size
    size = max(1, int(os.getenv("AGENT_MAX_CONCURRENCY", "2") or 2))
    if _sem is None or _sem_size != size:
        _sem, _sem_size = asyncio.Semaphore(size), size
    return _sem


def shadow_log_path() -> Path:
    return Path(os.getenv("AGENT_SHADOW_LOG") or (_REPO_ROOT / "data" / ".agent" / "shadow.jsonl"))


def append_trace(record: dict) -> None:
    try:
        path = shadow_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _log_lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning(f"agent trace write failed: {e}")


def _build_ctx(*, conversation_filter, explicit_filters, user_id, bundle_budget_cny):
    from app.agent.tools import ToolContext, resolve_session_constraints

    return ToolContext(
        session=resolve_session_constraints(conversation_filter, explicit_filters),
        user_id=user_id,
        bundle_budget_cny=bundle_budget_cny,
    )


async def _run_locked(user_text, *, route, conversation_filter, explicit_filters, user_id,
                      prior_turns, provider):
    """拿到并发闸才跑;闸满返回 (None, "busy")。"""
    sem = _semaphore()
    if sem.locked():
        return None, "busy"
    async with sem:
        from app.agent.graph import run_agent

        ctx = _build_ctx(conversation_filter=conversation_filter, explicit_filters=explicit_filters,
                         user_id=user_id, bundle_budget_cny=route.bundle_budget_cny)
        res = await run_agent(user_text, provider=provider, ctx=ctx,
                              prior_turns=prior_turns, route_reason=route.reason)
        return res, res.error


async def try_agent_products(user_text: str, *, route, conversation_filter, explicit_filters,
                             user_id, prior_turns, provider):
    """AGENT_PATH=on:返回 (products, trace);任何失败返回 ([], trace),调用方走快路。"""
    t0 = time.perf_counter()
    try:
        res, err = await _run_locked(user_text, route=route, conversation_filter=conversation_filter,
                                     explicit_filters=explicit_filters, user_id=user_id,
                                     prior_turns=prior_turns, provider=provider)
    except Exception as e:  # noqa: BLE001
        res, err = None, f"runtime_error:{type(e).__name__}"
    if res is None:
        trace = {"route": route.reason, "error": err,
                 "latency_ms": round((time.perf_counter() - t0) * 1000)}
        products: list[dict] = []
    else:
        trace = res.trace
        products = res.products if res.ok else []
    # on 模式同样落一行 trace(fallback=True 表示本次回退了快路),供线上对比
    if os.getenv("AGENT_TRACE_ON_MODE", "1") == "1":
        append_trace({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "on", "route": route.reason,
            "query_preview": (user_text or "")[:80], "fallback": not products,
            "agent_product_ids": trace.get("product_ids"), "dropped_ids": trace.get("dropped_ids"),
            "tool_calls": [{k: c.get(k) for k in ("name", "arguments", "result_ids", "notes", "error", "ms")}
                           for c in trace.get("tool_calls") or []],
            "rounds": trace.get("rounds"), "llm_calls": trace.get("llm_calls"),
            "usage": trace.get("usage"), "stop_reason": trace.get("stop_reason"),
            "latency_ms": trace.get("latency_ms"), "error": trace.get("error"),
        })
    return products, trace


def schedule_shadow(user_text: str, *, route, conversation_filter, explicit_filters, user_id,
                    prior_turns, provider, fast_product_ids: list[str]) -> None:
    """AGENT_PATH=shadow:后台跑一次,结果只写 JSONL,绝不影响本次响应。"""
    async def _job():
        t0 = time.perf_counter()
        record: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": "shadow",
            "route": route.reason,
            "query_preview": (user_text or "")[:80],
            "fast_product_ids": list(fast_product_ids or []),
        }
        try:
            res, err = await _run_locked(user_text, route=route, conversation_filter=conversation_filter,
                                         explicit_filters=explicit_filters, user_id=user_id,
                                         prior_turns=prior_turns, provider=provider)
            if res is None:
                record.update(error=err, latency_ms=round((time.perf_counter() - t0) * 1000))
            else:
                tr = res.trace
                record.update(
                    agent_product_ids=res.product_ids,
                    dropped_ids=res.dropped_ids,
                    tool_calls=[{k: c.get(k) for k in ("name", "arguments", "result_ids", "notes", "error", "ms")}
                                for c in tr.get("tool_calls") or []],
                    rounds=tr.get("rounds"), llm_calls=tr.get("llm_calls"), usage=tr.get("usage"),
                    stop_reason=tr.get("stop_reason"), latency_ms=tr.get("latency_ms"), error=res.error,
                )
        except Exception as e:  # noqa: BLE001
            record.update(error=f"shadow_error:{type(e).__name__}")
        append_trace(record)

    try:
        task = asyncio.get_running_loop().create_task(_job())
    except RuntimeError:
        return
    _shadow_tasks.add(task)            # 持有引用,防止任务被 GC 提前回收
    task.add_done_callback(_shadow_tasks.discard)
