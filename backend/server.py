import os
import sys

# Add the current directory to Python path to allow absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# DUPLICATE ROUTE MOUNT MAP
# =============================================================================
# Several routers are intentionally (or accidentally) mounted at more than one
# URL prefix. This doubles the attack surface and causes rate-limit counters
# (keyed by path in SlowAPI) to be tracked separately per prefix, effectively
# doubling a caller's quota when alternating between the two paths.
#
# Removal of root/legacy mounts requires a coordinated mobile-app release so
# that clients are updated to use the canonical /api/v1/ prefix before the
# legacy path is removed. DO NOT remove any root mounts without that release.
#
# Legend:
#   [INTENTIONAL]  — explicitly designed; a known trade-off with a documented
#                    reason. Root mount stays until mobile release.
#   [DUPLICATE]    — same router reachable at two URL prefixes; the root/legacy
#                    mount is deprecated and will be removed after mobile update.
#   [INFRA]        — infra-only path (health probe, metrics scraper, websocket);
#                    not a business-logic duplicate.
#
# Router                   | Canonical path          | Deprecated legacy path       | Status
# -------------------------|-------------------------|------------------------------|-------------------
# rides_router             | /api/v1/rides/...       | (none)                       | single mount
# documents_router         | /api/v1/drivers/...     | (none)                       | single mount
# admin_documents_router   | /api/v1/documents/...   | (none)                       | single mount
# drivers_router           | /api/v1/drivers/...     | (none)                       | single mount
# admin_router             | /api/v1/admin/...       | /api/admin/...               | [DUPLICATE]
# corporate_accounts_router| /api/v1/admin/corp../.. | /api/admin/corp../...        | [DUPLICATE]
# users_router             | /api/v1/users/...       | (none)                       | single mount
# addresses_router         | /api/v1/addresses/...   | (none)                       | single mount
# payments_router          | /api/v1/payments/...    | (none)                       | single mount
# notifications_router     | /api/v1/notifications/..| (none)                       | single mount
# fares_router             | /api/v1/fares/...       | (none)                       | single mount
# promotions_router        | /api/v1/promotions/...  | (none)                       | single mount
# disputes_router          | /api/v1/disputes/...    | (none)                       | single mount
# favorites_router         | /api/v1/favorites/...   | (none)                       | single mount
# loyalty_router           | /api/v1/loyalty/...     | (none)                       | single mount
# wallet_router            | /api/v1/wallet/...      | (none)                       | single mount
# fare_split_router        | /api/v1/fare-split/...  | (none)                       | single mount
# quests_router            | /api/v1/quests/...      | (none)                       | single mount
# webhooks_router          | /api/v1/webhooks/...    | (none)                       | single mount
# upload_router            | /api/v1/upload          | (none)                       | single mount
# support_router           | /api/v1/support/...     | (none)                       | single mount
# admin_support_router     | /api/v1/support/...     | (none)                       | single mount
# support_chat_router      | /api/v1/support/...     | (none)                       | single mount
# pricing_router           | /api/v1/pricing/...     | (none)                       | single mount
# faqs_router              | /api/v1/faqs/...        | (none)                       | single mount
# legal_documents_router   | /api/v1/legal/...       | (none)                       | single mount
# safety_router            | /api/v1/safety/...      | (none)                       | single mount
# service_areas_router     | /api/v1/service-areas/..| (none)                       | single mount
# auth_router              | /api/v1/auth/...        | /api/auth/...                | [DUPLICATE]
# settings_router          | /api/v1/settings/...    | /settings/... /company-info  | [INTENTIONAL]*
# websocket_router         | /ws/...                 | (none)                       | [INFRA]
# admin_auth_router        | /api/admin/auth/...     | (none)                       | single mount
# corporate_wallet_router  | /api/admin/corp-acct/.. | (none)                       | single mount
# corporate_company_router | /company/{id}/...       | (none)                       | [INTENTIONAL]**
# corporate_rider_router   | /rider/work-profile/... | (none)                       | [INTENTIONAL]**
# files_router             | /api/v1/documents/...   | /api/documents/...           | [DUPLICATE]***
# monitoring_router        | /api/admin/monitoring/..| (none)                       | single mount
#
# * settings_router: mobile apps call /settings/legal directly without a prefix.
#   Rate-limit doubling is explicitly accepted (public read-only endpoint).
#   Root mount stays until a coordinated mobile release.
#
# ** corporate_company_router / corporate_rider_router: rider app calls
#    /company/{id}/policy, /company/{id}/allowances, /rider/work-profile/*
#    without an /api prefix (verified in workProfileStore.ts). Root mount
#    stays until a coordinated mobile release migrates those calls.
#
# *** files_router: /api/documents/{id} is used by the admin dashboard;
#    /api/v1/documents/{id} resolves legacy driver_documents rows whose
#    document_url was written by the old base64-in-DB upload path. Both
#    mounts are intentional; the /api mount is the older legacy one and
#    should be removed when the admin dashboard is updated to use /api/v1.
#
# Deprecation strategy: a middleware (see DeprecatedRootPathMiddleware below)
# adds the response header  X-Spinr-Deprecated: true  and emits a WARNING log
# for any request hitting a known deprecated root/legacy path. Monitor the
# `[DEPRECATED_ROUTE]` log lines in Railway to measure traffic before removing.
# =============================================================================

import logging as _logging

from fastapi import APIRouter, FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Request
from starlette.responses import Response as _Response

from core.config import settings
from core.lifespan import lifespan
from core.middleware import init_middleware
from core.security import init_firebase

_depr_logger = _logging.getLogger("spinr.deprecated_routes")

# Paths (or path prefixes) that are served at a legacy mount AND also exist
# under /api/v1/. Any request whose URL path starts with one of these strings
# (and does NOT start with /api/v1/ or /api/) triggers a deprecation warning.
# Update this set when legacy mounts are removed.
_DEPRECATED_ROOT_PREFIXES: frozenset[str] = frozenset(
    {
        # auth_router: canonical is /api/v1/auth/..., legacy root is /api/auth/...
        # (tracked separately; /api/ prefix handled below as _DEPRECATED_API_PREFIXES)
        # settings_router: canonical is /api/v1/settings/..., legacy root is /settings/...
        "/settings",
        "/company-info",
    }
)

# Paths served at /api/... that are also reachable at /api/v1/...
# Rate-limit counters split across these two prefixes double the effective
# quota for callers who alternate between them.
_DEPRECATED_API_PREFIXES: frozenset[str] = frozenset(
    {
        # auth_router: canonical /api/v1/auth/..., legacy /api/auth/...
        "/api/auth",
        # admin_router: canonical /api/v1/admin/..., legacy /api/admin/...
        # (admin_auth_router and monitoring_router are /api/-only, NOT duplicates)
        # Note: we can't flag all /api/admin/ without also hitting the /api/-only
        # admin routes, so we exclude those sub-paths explicitly in the middleware.
        # files_router: canonical /api/v1/documents/..., legacy /api/documents/...
        "/api/documents",
        # corporate_accounts_router:
        #   canonical /api/v1/admin/corporate-accounts/...
        #   legacy    /api/admin/corporate-accounts/...
        "/api/admin/corporate-accounts",
    }
)

# Admin-only paths that exist ONLY at /api/admin/... and have no /api/v1/ twin.
# These must be excluded so we don't mistakenly flag them as deprecated.
_API_ADMIN_ONLY_PATHS: frozenset[str] = frozenset(
    {
        "/api/admin/auth",
        "/api/admin/monitoring",
        "/api/admin/corporate-accounts/wallet",  # corporate_wallet_router
    }
)


class DeprecatedRootPathMiddleware(BaseHTTPMiddleware):
    """Add X-Spinr-Deprecated: true header and emit a WARNING log for any
    request that hits a known legacy/root-mount path that is also reachable
    at the canonical /api/v1/ prefix.

    This middleware is purely observational — it does NOT redirect or block
    any traffic. Monitor the [DEPRECATED_ROUTE] log lines to measure usage
    of the old paths before removing the duplicate mounts.
    """

    async def dispatch(self, request: _Request, call_next) -> _Response:  # type: ignore[override]
        path = request.url.path

        deprecated = False

        # Check deprecated root paths (e.g. /settings/...)
        for prefix in _DEPRECATED_ROOT_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                deprecated = True
                break

        # Check deprecated /api/ paths that are also at /api/v1/
        if not deprecated:
            for prefix in _DEPRECATED_API_PREFIXES:
                if path == prefix or path.startswith(prefix + "/"):
                    # Make sure this is not an /api/-only path (no /api/v1/ twin)
                    is_api_only = any(path == excl or path.startswith(excl + "/") for excl in _API_ADMIN_ONLY_PATHS)
                    if not is_api_only:
                        deprecated = True
                        break

        response: _Response = await call_next(request)

        if deprecated:
            # Derive the canonical /api/v1/ equivalent for the log message.
            if path.startswith("/api/"):
                # /api/foo/... → /api/v1/foo/...
                canonical = "/api/v1/" + path[len("/api/") :]
            else:
                # /settings/... → /api/v1/settings/...
                canonical = "/api/v1" + path

            _depr_logger.warning(
                "[DEPRECATED_ROUTE] %s — use %s instead",
                path,
                canonical,
            )
            response.headers["X-Spinr-Deprecated"] = "true"

        return response


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

# Register the deprecation-header middleware AFTER the main middleware stack so
# it runs on the way out (response phase) and can append the response header.
# It is lightweight — O(prefixes) string comparison per request.
app.add_middleware(DeprecatedRootPathMiddleware)

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
# DUPLICATE MOUNT — auth_router is also served at /api/v1/auth/...; remove this
# /api/auth/... mount after mobile apps are updated to use /api/v1/ prefix.
app.include_router(auth_router, prefix="/api")

# WebSocket routes — mounted at root so the path /ws/{client_type}/{client_id} is served directly
app.include_router(websocket_router)

# DUPLICATE MOUNT (INTENTIONAL) — settings_router is also served at
# /api/v1/settings/... and /api/v1/company-info. The root mount (/settings/...,
# /company-info) is kept because the rider app calls /settings/legal directly
# without an /api prefix. Remove root mount after mobile apps are updated to use
# /api/v1/ prefix. Rate-limit doubling is explicitly accepted for this
# public+read-only endpoint (see duplicate mount map at top of file).
app.include_router(settings_router)
app.include_router(settings_router, prefix="/api/v1")

# Mount admin routes under /api so the admin dashboard can reach them at /api/admin/...
# DUPLICATE MOUNT — admin_router (prefix /admin) is also served via v1_api_router at
# /api/v1/admin/...; remove this /api/admin/... mount after the admin dashboard is
# updated to use /api/v1/ prefix.
app.include_router(admin_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")  # /api/admin/auth/... — /api only, NOT a duplicate
# DUPLICATE MOUNT — corporate_accounts_router (prefix /admin/corporate-accounts) is also
# served via v1_api_router at /api/v1/admin/corporate-accounts/...; remove this
# /api/admin/corporate-accounts/... mount after the admin dashboard uses /api/v1/ prefix.
app.include_router(corporate_accounts_router, prefix="/api")
app.include_router(
    corporate_wallet_router, prefix="/api"
)  # /api/admin/corporate-accounts/... — /api only, NOT a duplicate
# Corporate member/allowance/domain endpoints served at root (`/company/{id}/...`)
# because the rider app calls /company/{id}/policy and /company/{id}/allowances
# without an /api prefix (verified in workProfileStore.ts). Do not remove until
# a coordinated mobile release migrates those calls to /api/company/{id}/...
app.include_router(corporate_company_router)
app.include_router(corporate_rider_router)
# DUPLICATE MOUNT — files_router (prefix /documents) is served at both
# /api/documents/{id} (used by admin dashboard) and /api/v1/documents/{id}
# (resolves legacy driver_documents rows written by the old base64-in-DB upload
# path). Both mounts are intentional; /api/documents/... is the older mount and
# should be removed after the admin dashboard is updated to use /api/v1/ prefix.
app.include_router(files_router, prefix="/api")
app.include_router(files_router, prefix="/api/v1")
app.include_router(monitoring_router, prefix="/api")  # /api/admin/monitoring/... — /api only, NOT a duplicate

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
