import os
import sys

# Add the current directory to Python path to allow absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import APIRouter, FastAPI

from core.config import settings
from core.lifespan import lifespan
from core.middleware import init_middleware
from core.security import init_firebase
from documents import admin_documents_router, documents_router, files_router, upload_router
from features import admin_support_router, pricing_router, support_router
from routes.addresses import api_router as addresses_router
from routes.admin import admin_auth_router
from routes.admin import admin_router as admin_router
from routes.admin.monitoring import router as monitoring_router
from routes.auth import api_router as auth_router
from routes.corporate_accounts import router as corporate_accounts_router
from routes.disputes import api_router as disputes_router
from routes.drivers import api_router as drivers_router
from routes.fare_split import api_router as fare_split_router
from routes.fares import api_router as fares_router
from routes.favorites import api_router as favorites_router
from routes.loyalty import api_router as loyalty_router
from routes.notifications import api_router as notifications_router
from routes.payments import api_router as payments_router
from routes.promotions import api_router as promotions_router
from routes.quests import api_router as quests_router
from routes.rides import api_router as rides_router
from routes.settings import api_router as settings_router
from routes.users import api_router as users_router
from routes.wallet import api_router as wallet_router
from routes.webhooks import api_router as webhooks_router
from routes.websocket import router as websocket_router
from utils.error_handling import register_exception_handlers

# Initialize Firebase
init_firebase()

app = FastAPI(title="Spinr API", version="1.0.0", lifespan=lifespan, redirect_slashes=False)

# Initialize middleware
init_middleware(app)

# Register exception handlers so unhandled errors return JSON (with CORS
# headers) instead of falling through to Starlette's ServerErrorMiddleware,
# which emits plain-text 500s that look like CORS failures in the browser.
register_exception_handlers(app)

# Create v1 API router
v1_api_router = APIRouter()
v1_api_router.include_router(rides_router)
# documents_router MUST be included before drivers_router so that its specific
# paths (/drivers/requirements, /drivers/documents) are matched before the
# catch-all wildcard GET /drivers/{driver_id} in drivers_router.
v1_api_router.include_router(documents_router)
v1_api_router.include_router(admin_documents_router)
v1_api_router.include_router(drivers_router)
v1_api_router.include_router(admin_router)
v1_api_router.include_router(corporate_accounts_router)
v1_api_router.include_router(users_router)
v1_api_router.include_router(addresses_router)
v1_api_router.include_router(payments_router)
v1_api_router.include_router(notifications_router)
v1_api_router.include_router(fares_router)
v1_api_router.include_router(promotions_router)
v1_api_router.include_router(disputes_router)
v1_api_router.include_router(favorites_router)
v1_api_router.include_router(loyalty_router)
v1_api_router.include_router(wallet_router)
v1_api_router.include_router(fare_split_router)
v1_api_router.include_router(quests_router)
v1_api_router.include_router(webhooks_router)
v1_api_router.include_router(upload_router)
v1_api_router.include_router(support_router)
v1_api_router.include_router(admin_support_router)
v1_api_router.include_router(pricing_router)

# Include API routers
app.include_router(v1_api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")

# WebSocket routes — mounted at root so the path /ws/{client_type}/{client_id} is served directly
app.include_router(websocket_router)

# Public settings endpoints (GET /settings, GET /settings/legal). Mounted at
# root so mobile apps can call them without an auth token, and also at /api/v1
# for parity. The legal screen fetch uses backendUrl/settings/legal directly.
app.include_router(settings_router)
app.include_router(settings_router, prefix="/api/v1")

# Mount admin routes under /api so the admin dashboard can reach them at /api/admin/...
app.include_router(admin_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")
app.include_router(corporate_accounts_router, prefix="/api")
# files_router serves document files at /api/documents/{id} (used by admin dashboard)
app.include_router(files_router, prefix="/api")
app.include_router(monitoring_router, prefix="/api")

# Configure structured logging with Loguru
import sys  # noqa: E402

from loguru import logger  # noqa: E402

# Remove default handler and add custom JSON handler
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}",
    serialize=True,  # This enables JSON formatting
)

# Add file logging for production
logger.add("logs/app.log", rotation="500 MB", retention="7 days", level="INFO", serialize=True)

# Configure Sentry for error monitoring — imports are deferred inside the DSN
# guard so that sentry_sdk's starlette integration is never imported in
# environments where SENTRY_DSN is unset (avoids DidNotEnable crash in CI).
sentry_dsn = settings.sentry_dsn if hasattr(settings, "sentry_dsn") and settings.sentry_dsn else None

if sentry_dsn:
    import sentry_sdk  # noqa: E402
    from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: E402
    from sentry_sdk.integrations.logging import LoggingIntegration  # noqa: E402

    _StarletteMiddleware = None
    try:
        from sentry_sdk.integrations.starlette import StarletteMiddleware as _StarletteMiddleware
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Sentry Starlette integration unavailable: {exc}")

    integrations = [
        FastApiIntegration(transaction_style="url"),
        LoggingIntegration(event_level="ERROR", breadcrumb_level="WARNING"),
    ]
    if _StarletteMiddleware is not None:
        integrations.append(_StarletteMiddleware())

    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=integrations,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=settings.ENV if hasattr(settings, "ENV") else "production",
        send_default_pii=True,
    )
    logger.info("Sentry SDK initialized for error monitoring")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104
