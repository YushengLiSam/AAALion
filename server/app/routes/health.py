from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@router.get("/ready")
async def ready(request: Request):
    detail = getattr(request.app.state, "retrieval_warmup", {"status": "starting"})
    if not getattr(request.app.state, "retrieval_ready", False):
        return JSONResponse(status_code=503, content={"status": "not_ready", "retrieval": detail})
    return {"status": "ready", "retrieval": detail}
