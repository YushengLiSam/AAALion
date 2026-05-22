"""FastAPI entrypoint for AAALion-."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import chat, products, health


def create_app() -> FastAPI:
    app = FastAPI(
        title="AAALion- backend",
        version="0.1.0",
        description="RAG-based multimodal e-commerce shopping agent.",
    )

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

    # Serve seed images for the demo (production would put these behind a CDN).
    images_root = settings.repo_root / "data" / "seed"
    if images_root.exists():
        app.mount("/static", StaticFiles(directory=images_root), name="static")

    return app


app = create_app()
