"""FastAPI entrypoint for AAALion-."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import cache_stats, chat, products, health

log = logging.getLogger("startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.retrieval_ready = False
    app.state.retrieval_warmup = {"status": "warming"}
    try:
        from app.services.retrieval_readiness import warm_retrieval_pipeline

        detail = await asyncio.to_thread(warm_retrieval_pipeline)
    except Exception as exc:  # noqa: BLE001
        log.exception("Retrieval warmup failed")
        app.state.retrieval_warmup = {"status": "error", "message": str(exc)}
    else:
        app.state.retrieval_ready = True
        app.state.retrieval_warmup = {"status": "ready", **detail}
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AAALion- backend",
        version="0.1.0",
        description="RAG-based multimodal e-commerce shopping agent.",
        lifespan=lifespan,
    )
    app.state.retrieval_ready = False
    app.state.retrieval_warmup = {"status": "starting"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(products.router)
    app.include_router(cache_stats.router)

    # Serve seed images for the demo (production would put these behind a CDN).
    images_root = settings.repo_root / "data" / "seed"
    if images_root.exists():
        app.mount("/static", StaticFiles(directory=images_root), name="static")

    return app


app = create_app()
