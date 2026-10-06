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
    body: dict = {"status": "ready", "retrieval": detail, "fallbacks": degradation_stats()}
    if mode != "off":
        gate = await _vector_store_gate()
        body["vector_store_gate"] = {"mode": mode, **gate}
        if mode == "enforce" and not gate.get("ok"):
            body["status"] = "not_ready"
            body["reason"] = "vector_store_gate"
            return JSONResponse(status_code=503, content=body)
    return body
