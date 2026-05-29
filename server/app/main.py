"""FastAPI entrypoint for AAALion-."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import cache_stats, chat, currency, preferences, price_watch, products, health, repurchase

log = logging.getLogger("startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.retrieval_ready = False
    app.state.retrieval_warmup = {"status": "warming"}
    # Repurchase reminder schema: cheap, sync, idempotent — init before
    # serving any requests so the first /repurchase/* call doesn't race
    # with table creation.
    try:
        from app.services.repurchase_db import init_schema as _init_repurchase

        await asyncio.to_thread(_init_repurchase)
    except Exception:  # noqa: BLE001
        log.exception("Repurchase schema init failed (route will 500 on use)")
    # R9.A.4 — price-watch schema init. Same pattern as repurchase.
    try:
        from app.services.price_watch_db import init_schema as _init_price_watch

        await asyncio.to_thread(_init_price_watch)
    except Exception:  # noqa: BLE001
        log.exception("Price-watch schema init failed (route will 500 on use)")
    # R9.B — preference-learning schema init (proposal #12).
    try:
        from app.services.preferences_db import init_schema as _init_preferences

        await asyncio.to_thread(_init_preferences)
    except Exception:  # noqa: BLE001
        log.exception("Preferences schema init failed (route will 500 on use)")
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
    app.include_router(repurchase.router)
    app.include_router(currency.router)
    app.include_router(price_watch.router)
    app.include_router(preferences.router)

    # Serve seed images for the demo (production would put these behind a CDN).
    images_root = settings.repo_root / "data" / "seed"
    if images_root.exists():
        app.mount("/static", StaticFiles(directory=images_root), name="static")

    return app


app = create_app()
