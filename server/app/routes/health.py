import asyncio
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


async def _vector_store_gate() -> dict:
    """在线程里跑(带缓存的)向量库门控;超时视为未就绪,不阻塞事件循环。"""
    from app.services.retrieval_readiness import gate_status

    try:
        timeout = float(os.getenv("RAG_READY_GATE_TIMEOUT_S", "10"))
    except ValueError:
        timeout = 10.0
    try:
        return await asyncio.wait_for(asyncio.to_thread(gate_status), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "reasons": [f"vector store gate timed out after {timeout:g}s"]}
    except Exception as exc:  # noqa: BLE001 - 门控本身出错也只能算"未就绪"
        return {"ok": False, "reasons": [f"vector store gate error: {type(exc).__name__}: {exc}"]}


def _effective_llm() -> dict:
    """当前进程**实际生效**的 LLM provider / 模型,便于核对线上到底在用哪个模型。

    只暴露 provider 名和模型名,绝不暴露 key / base_url。"实际生效"指 get_provider()
    建出来的那个单例:比如 LLM_PROVIDER=tokenrouter 但 key 缺失,这里会如实显示 echo
    (requested 字段保留原始配置,两者不一致一眼就能看出来)。构造 provider 只建客户端对象,
    不发任何网络请求。
    """
    info: dict = {"requested": (os.getenv("LLM_PROVIDER") or "").strip().lower() or "auto"}
    try:
        from app.services.llm_provider import get_provider

        provider = get_provider()
    except Exception as exc:  # noqa: BLE001 - 只是展示信息,不能拖垮 /ready
        info.update({"provider": "unavailable", "model": None, "error": type(exc).__name__})
        return info
    info["provider"] = getattr(provider, "name", type(provider).__name__)
    info["model"] = getattr(provider, "_model", None)
    agent_model = (os.getenv("AGENT_LLM_MODEL") or "").strip()
    if agent_model and getattr(provider, "supports_tools", False):
        info["agent_model"] = agent_model
    return info


@router.get("/ready")
async def ready(request: Request):
    detail = getattr(request.app.state, "retrieval_warmup", {"status": "starting"})
    if not getattr(request.app.state, "retrieval_ready", False):
        return JSONResponse(status_code=503, content={"status": "not_ready", "retrieval": detail})

    from app.services.retrieval_readiness import degradation_stats, gate_mode

    # P0.4 —— 向量库门控(RAG_READY_GATE=off|report|enforce,默认 off = 与之前一致)
    # 以及降级计数。enforce 模式下门控不过返回 503:autodeploy 的 `curl -sf | grep ready`
    # 遇到非 2xx 直接判失败并回滚,外部探活也据此告警。
    mode = gate_mode()
    body: dict = {
        "status": "ready",
        "retrieval": detail,
        "fallbacks": degradation_stats(),
        "llm": _effective_llm(),
    }
    if mode != "off":
        gate = await _vector_store_gate()
        body["vector_store_gate"] = {"mode": mode, **gate}
        if mode == "enforce" and not gate.get("ok"):
            body["status"] = "not_ready"
            body["reason"] = "vector_store_gate"
            return JSONResponse(status_code=503, content=body)
    return body
