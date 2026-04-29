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
from routes.corporate_company import router as corporate_company_router
from routes.corporate_rider import router as corporate_rider_router
from routes.corporate_wallet import router as corporate_wallet_router
from routes.disputes import api_router as disputes_router
from routes.drivers import api_router as drivers_router
from routes.faqs import api_router as faqs_router
from routes.fare_split import api_router as fare_split_router
from routes.fares import api_router as fares_router
from routes.favorites import api_router as favorites_router
from routes.legal_documents import api_router as legal_documents_router
from routes.loyalty import api_router as loyalty_router
from routes.notifications import api_router as notifications_router
from routes.payments import api_router as payments_router
from routes.promotions import api_router as promotions_router
from routes.quests import api_router as quests_router
from routes.rides import api_router as rides_router
from routes.safety import api_router as safety_router
from routes.service_areas import api_router as service_areas_router
from routes.settings import api_router as settings_router
from routes.support import api_router as support_chat_router
from routes.users import api_router as users_router
from routes.wallet import api_router as wallet_router
from routes.webhooks import api_router as webhooks_router
from routes.websocket import router as websocket_router
from utils.error_handling import register_exception_handlers

# Initialize Firebase
init_firebase()

app = FastAPI(title="Spinr API", version="1.0.0", lifespan=lifespan, redirect_slashes=False)


# Railway's healthcheckPath in railway.json is /health. If that endpoint
# returns anything other than 2xx the deployment never goes live and every
# request to the public domain is answered with "Application failed to
# respond". Mount it on the root app (not behind /api) so the probe hits it
# before any auth middleware.
@app.get("/health")
async def health():
    return {"status": "healthy"}


# Prometheus-style metrics exposition. Scraped by Grafana / Railway
# observability add-ons. No auth (it's numbers only — no PII) and
# mounted at the root so the scraper doesn't need /api/v1 knowledge.
# Counters cover: DB retries by policy/reason, cache hit/miss by
# prefix, circuit-breaker state, call-level totals, and Redis stats.
# See utils/metrics.py for the counter definitions.
from fastapi import Response as _MetricsResponse  # noqa: E402


@app.get("/metrics")
async def metrics() -> _MetricsResponse:
    from utils.metrics import render_prometheus, set_gauge
    from utils.redis_client import get_redis_stats

    # Refresh Redis gauges on each scrape. INFO is O(1) on Redis so
    # this is cheap; exposing them as gauges lets the Prometheus
    # server compute rates (eviction rate, hit rate) server-side.
    try:
        rs = await get_redis_stats()
        if rs.get("connected"):
            set_gauge("spinr_redis_used_memory_bytes", rs.get("used_memory_bytes") or 0)
            set_gauge("spinr_redis_maxmemory_bytes", rs.get("maxmemory_bytes") or 0)
            if rs.get("used_memory_percent") is not None:
                set_gauge("spinr_redis_used_memory_percent", rs["used_memory_percent"])
            set_gauge("spinr_redis_total_keys", rs.get("total_keys") or 0)
            set_gauge("spinr_redis_connected_clients", rs.get("connected_clients") or 0)
            set_gauge("spinr_redis_uptime_seconds", rs.get("uptime_seconds") or 0)
            # Counter-like values from INFO stats. Exposed as gauges
            # here because Prometheus can compute rate() on either;
            # keeping them gauges avoids lying about reset semantics.
            set_gauge("spinr_redis_keyspace_hits", rs.get("keyspace_hits_total") or 0)
            set_gauge("spinr_redis_keyspace_misses", rs.get("keyspace_misses_total") or 0)
            set_gauge("spinr_redis_evicted_keys", rs.get("evicted_keys_total") or 0)
            set_gauge("spinr_redis_expired_keys", rs.get("expired_keys_total") or 0)
            set_gauge("spinr_redis_connected", 1)
        else:
            set_gauge("spinr_redis_connected", 0)
    except Exception:
        # Never let a metrics scrape blow up — return whatever counters
        # have been recorded so far.
        set_gauge("spinr_redis_connected", 0)

    return _MetricsResponse(
        content=render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


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
v1_api_router.include_router(support_chat_router)
v1_api_router.include_router(pricing_router)
v1_api_router.include_router(faqs_router)
v1_api_router.include_router(legal_documents_router)
v1_api_router.include_router(safety_router)
v1_api_router.include_router(service_areas_router)

# Include API routers
app.include_router(v1_api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")

# WebSocket routes — mounted at root so the path /ws/{client_type}/{client_id} is served directly
app.include_router(websocket_router)

# Public settings endpoints (GET /settings, GET /settings/legal). Mounted at
# root so mobile apps can call them without an auth token, and also at /api/v1
# for parity. The legal screen fetch uses backendUrl/settings/legal directly.
# Rate-limit note: double-mounting means slowapi tracks each prefix separately.
# The settings endpoint is public+read-only so the doubled effective rate is
# acceptable; add a shared key_func if this ever needs tighter control.
app.include_router(settings_router)
app.include_router(settings_router, prefix="/api/v1")

# Mount admin routes under /api so the admin dashboard can reach them at /api/admin/...
app.include_router(admin_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")
app.include_router(corporate_accounts_router, prefix="/api")
app.include_router(corporate_wallet_router, prefix="/api")
# Corporate member/allowance/domain endpoints served at root (`/company/{id}/...`)
# because the rider app calls /company/{id}/policy and /company/{id}/allowances
# without an /api prefix (verified in workProfileStore.ts). Do not remove until
# a coordinated mobile release migrates those calls to /api/company/{id}/...
app.include_router(corporate_company_router)
app.include_router(corporate_rider_router)
# files_router serves document files at /api/documents/{id} (used by admin dashboard).
# Also mounted under /api/v1 so legacy driver_documents rows whose document_url was
# written as /api/v1/documents/{id} by the old base64-in-DB upload path keep resolving.
app.include_router(files_router, prefix="/api")
app.include_router(files_router, prefix="/api/v1")
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

# No file logging in production — Railway captures stdout/stderr and exposes
# them in the dashboard + `railway logs`. Writing to a local file on the
# container's ephemeral disk wastes memory, gets wiped on every redeploy, and
# makes logs invisible to the platform's log aggregator.

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
        # PIPEDA: never send IP, cookies, or auth headers to Sentry.
        send_default_pii=False,
    )

    # Bridge loguru → Sentry. The LoggingIntegration above only captures
    # stdlib `logging` records; the rest of the backend uses loguru and
    # would otherwise be invisible in Sentry (including the high-signal
    # REFRESH TOKEN REUSE DETECTED alert in utils/refresh_tokens.py).
    def _loguru_sentry_sink(message: "Any") -> None:  # noqa: ANN401, F821
        record = message.record
        exc_info = record["exception"]
        if exc_info is not None and exc_info.value is not None:
            sentry_sdk.capture_exception(exc_info.value)
        else:
            sentry_sdk.capture_message(record["message"], level="error")

    logger.add(_loguru_sentry_sink, level="ERROR")
    logger.info("Sentry SDK initialized for error monitoring")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104
