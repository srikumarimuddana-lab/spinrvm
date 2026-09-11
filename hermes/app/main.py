"""Hermes — Spinr ops agent. FastAPI app factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("Hermes starting (env=%s)", settings.ENV)

    # Register Telegram webhook on startup if configured
    if settings.TELEGRAM_BOT_TOKEN and settings.HERMES_PUBLIC_URL:
        from app.integrations.telegram import register_webhook
        await register_webhook()

    yield

    logger.info("Hermes shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Hermes — Spinr Ops Agent",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [settings.HERMES_PUBLIC_URL],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Routes ---
    from app.web.dashboard import router as web_router
    from app.web.config_api import router as config_router
    from app.integrations.telegram import router as telegram_router
    from app.integrations.zoho_cliq import router as cliq_router
    from app.integrations.zoho_desk import router as desk_router
    from app.integrations.spinr import router as spinr_router
    from app.mcp.server import router as mcp_router

    app.include_router(web_router)
    app.include_router(config_router, prefix="/api", tags=["config"])
    app.include_router(telegram_router, prefix="/telegram", tags=["telegram"])
    app.include_router(cliq_router, prefix="/zoho/cliq", tags=["zoho-cliq"])
    app.include_router(desk_router, prefix="/zoho/desk", tags=["zoho-desk"])
    app.include_router(spinr_router, prefix="/spinr", tags=["spinr"])
    app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "hermes",
            "env": settings.ENV,
            "integrations": {
                "telegram": bool(settings.TELEGRAM_BOT_TOKEN),
                "zoho_cliq": bool(settings.ZOHO_CLIQ_BOT_API_URL),
                "zoho_desk": bool(settings.ZOHO_DESK_ORG_ID),
                "spinr_backend": bool(settings.SPINR_API_KEY),
            },
        }

    return app


app = create_app()
