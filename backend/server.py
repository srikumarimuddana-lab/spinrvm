import hmac
import logging as _logging
import os
import sys

# Add the current directory to Python path to allow absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import APIRouter, Depends, FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Request
from starlette.responses import Response as _Response

from core.config import settings
from core.lifespan import lifespan
from core.middleware import init_middleware
from core.security import init_firebase
from dependencies import require_module

_depr_logger = _logging.getLogger("spinr.deprecated_routes")

# Paths (or path prefixes) that are served at a legacy mount AND also exist
# under /api/v1/. Any request whose URL path starts with one of these strings
# (and does NOT start with /api/v1/ or /api/) triggers a deprecation warning.
# Update this set when legacy mounts are removed.
_DEPRECATED_ROOT_PREFIXES: frozenset[str] = frozenset(
    {
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

        # Check deprecated /api/ paths that are also at /api/v1/.
        # Exclude /api/admin/*: those are App-Check-EXEMPT, but their /api/v1/admin
        # twin is App-Check-ENFORCED (core/middleware.py), so a browser client
        # (admin dashboard) cannot migrate to it. Flagging them is pure noise, not
        # an actionable deprecation signal, so we neither log nor header them.
        if not deprecated and not path.startswith("/api/admin/"):
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
                canonical = "/api/v1/" + path[len("/api/") :]
            else:
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
from routes.ai import api_router as ai_router
from routes.auth import api_router as auth_router
from routes.corporate_accounts import router as corporate_accounts_router
from routes.corporate_company import router as corporate_company_router
from routes.corporate_company_bookings import router as corporate_company_bookings_router
from routes.corporate_company_kyb import router as corporate_company_kyb_router
from routes.corporate_rider import router as corporate_rider_router
from routes.corporate_signup import router as corporate_signup_router
from routes.corporate_subscriptions import router as corporate_subscriptions_router
from routes.corporate_wallet import router as corporate_wallet_router
from routes.disputes import api_router as disputes_router
from routes.drivers import api_router as drivers_router
from routes.faqs import api_router as faqs_router
from routes.fares import api_router as fares_router
from routes.favorites import api_router as favorites_router
from routes.legal_documents import api_router as legal_documents_router
from routes.lost_and_found import api_router as lost_and_found_router
from routes.loyalty import api_router as loyalty_router
from routes.maps_proxy import api_router as maps_router
from routes.marketing import api_router as marketing_router
from routes.notifications import api_router as notifications_router
from routes.offer_card import router as offer_card_router
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


# /health is the readiness probe every platform gate hits: fly.toml
# [[http_service.checks]], railway.json healthcheckPath, both Dockerfile
# HEALTHCHECKs, and the CI post-deploy smoke test. It must verify the DB is
# actually reachable — otherwise a replica whose Supabase connection is dead
# (or whose circuit breaker is open) keeps returning 200, stays in the
# load-balancer rotation, and answers every real request with a 503, while a
# bad rolling deploy gets promoted. (F1: previously this returned a static
# {"status":"healthy"} unconditionally.) The DB ping is cached for a few
# seconds and time-bounded so frequent probes across replicas add no real load
# and can't hang. Loop-staleness is intentionally NOT part of this probe — a
# stale background loop must not pull a serving replica out of rotation; that
# is covered separately by the loop watchdog alert.
_HEALTH_CACHE_TTL = 5.0  # seconds — bound DB-ping load under frequent probing
_HEALTH_PING_TIMEOUT = 3.0  # seconds — never let a hung DB hang the probe
_health_cache: dict = {"at": 0.0, "ok": False, "detail": {}}


async def _db_ready() -> "tuple[bool, dict]":
    import asyncio
    import time as _time

    import db_supabase

    now = _time.monotonic()
    if now - _health_cache["at"] < _HEALTH_CACHE_TTL:
        return _health_cache["ok"], _health_cache["detail"]

    ok = False
    detail: dict = {}
    try:
        info = await asyncio.wait_for(db_supabase.ping(), timeout=_HEALTH_PING_TIMEOUT)
        ok = True
        if isinstance(info, dict):
            # Only non-sensitive telemetry — safe on an unauthenticated probe.
            detail = {k: info[k] for k in ("ping_ms", "circuit_state") if k in info}
    except Exception as exc:
        # Full error is logged server-side; the public body stays generic so the
        # health endpoint never leaks DB internals.
        _logging.getLogger(__name__).error(f"/health DB readiness check failed: {exc}")

    _health_cache.update(at=now, ok=ok, detail=detail)
    return ok, detail


@app.get("/health")
async def health():
    from starlette.responses import JSONResponse

    ok, detail = await _db_ready()
    if ok:
        return {"status": "healthy", "db": {"status": "ok", **detail}}
    return JSONResponse(
        status_code=503,
        content={"status": "unhealthy", "db": {"status": "error"}},
    )


# Prometheus-style metrics exposition. Scraped by Grafana / Railway
# observability add-ons. No auth (it's numbers only — no PII) and
# mounted at the root so the scraper doesn't need /api/v1 knowledge.
# Counters cover: DB retries by policy/reason, cache hit/miss by
# prefix, circuit-breaker state, call-level totals, and Redis stats.
# See utils/metrics.py for the counter definitions.
from fastapi import Response as _MetricsResponse  # noqa: E402


def _metrics_token() -> str:
    """Bearer token required to scrape /metrics, from METRICS_AUTH_TOKEN env.

    Empty = unauthenticated scrape allowed (preserves existing Grafana/Railway
    setups). Set the env var to lock the endpoint down — recommended before
    launch so operational signal (error rates, Redis health, traffic) isn't
    readable by anyone on the internet.
    """
    return os.getenv("METRICS_AUTH_TOKEN", "").strip()


@app.get("/metrics")
async def metrics(request: _Request) -> _MetricsResponse:
    from fastapi import HTTPException

    _token = _metrics_token()
    if _token:
        auth = request.headers.get("authorization", "")
        presented = auth[7:].strip() if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
        if not hmac.compare_digest(presented, _token):
            raise HTTPException(status_code=401, detail="Unauthorized")
    elif settings.ENV.lower() == "production":
        # FAIL CLOSED: an unset token in production must NOT serve error rates,
        # traffic volume, and Redis internals to the public internet. Refuse the
        # scrape (503) instead of warning-and-serving — the operator sets
        # METRICS_AUTH_TOKEN to enable it. Non-production is unauthenticated by
        # design for local Prometheus.
        _logging.getLogger("spinr.metrics").error(
            "/metrics requested in production without METRICS_AUTH_TOKEN set — refusing. "
            "Set METRICS_AUTH_TOKEN to enable scraping."
        )
        raise HTTPException(status_code=503, detail="Metrics endpoint not configured")

    from utils.loop_monitor import get_heartbeat_epochs
    from utils.metrics import render_prometheus, set_gauge
    from utils.redis_client import get_redis_stats

    # Background-loop liveness as a gauge, refreshed at scrape time (same
    # pattern as the Redis gauges below).  ADR-010 §3 wants this as a second,
    # independent stall-detection path alongside the in-app loop_watchdog:
    # the watchdog posts to ALERT_WEBHOOK_URL and does not depend on the
    # metrics pipeline, while this gives dashboard visibility and survives the
    # watchdog itself dying.  Alert as:
    #   time() - spinr_loop_heartbeat_timestamp_seconds > 2 * expected_interval
    # Evaluate per provider, never summed — every loop runs on BOTH Fly and
    # Railway by design (ADR-010 §4), so a healthy Fly loop would otherwise
    # mask a dead Railway one.
    try:
        for _loop_name, _epoch in get_heartbeat_epochs().items():
            set_gauge("spinr_loop_heartbeat_timestamp_seconds", _epoch, {"loop": _loop_name})
    except Exception:
        # Never let loop bookkeeping break a scrape — the counters below are
        # still worth serving even if heartbeat state is unreadable. Logged at
        # error (not swallowed) per CLAUDE.md: this is in-memory dict work under
        # a lock, so a failure here means something genuinely unexpected and
        # must not be silent just because the scrape survived it.
        _logging.getLogger("spinr.metrics").error(
            "Failed to export loop heartbeat gauges; serving remaining metrics",
            exc_info=True,
        )

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
v1_api_router.include_router(lost_and_found_router)
# documents_router MUST be included before drivers_router so that its specific
# paths (/drivers/requirements, /drivers/documents) are matched before the
# catch-all wildcard GET /drivers/{driver_id} in drivers_router.
v1_api_router.include_router(documents_router)
v1_api_router.include_router(admin_documents_router)
v1_api_router.include_router(drivers_router)
v1_api_router.include_router(admin_router)
# corporate_wallet_router and corporate_subscriptions_router each expose static
# single-segment paths (/wallet-portfolio, /subscription-plans) under the same
# /admin/corporate-accounts prefix as corporate_accounts_router's catch-all
# GET /{account_id}. FastAPI matches by registration order, so both must be
# included BEFORE corporate_accounts_router or those static paths get swallowed
# by /{account_id} (a company named e.g. "wallet-portfolio" would 404 instead).
# Canonical /api/v1 twin for the corporate wallet admin routes
# (/admin/corporate-accounts/{id}/wallet/...). Also mounted at /api below for
# backward compat; the admin dashboard now calls the /api/v1 paths.
v1_api_router.include_router(corporate_wallet_router, dependencies=[Depends(require_module("corporate_accounts"))])
v1_api_router.include_router(
    corporate_subscriptions_router, dependencies=[Depends(require_module("corporate_accounts"))]
)
v1_api_router.include_router(corporate_accounts_router, dependencies=[Depends(require_module("corporate_accounts"))])
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
v1_api_router.include_router(quests_router)
v1_api_router.include_router(webhooks_router)
v1_api_router.include_router(marketing_router)
v1_api_router.include_router(upload_router)
v1_api_router.include_router(support_router)
v1_api_router.include_router(admin_support_router)
v1_api_router.include_router(support_chat_router)
v1_api_router.include_router(ai_router)
v1_api_router.include_router(pricing_router)
v1_api_router.include_router(faqs_router)
v1_api_router.include_router(legal_documents_router)
v1_api_router.include_router(safety_router)
v1_api_router.include_router(service_areas_router)
v1_api_router.include_router(offer_card_router)
v1_api_router.include_router(maps_router)

# Include API routers
app.include_router(v1_api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")
# Company-portal auth namespace: the SAME auth handlers under an
# App-Check-exempt path (see _APP_CHECK_EXEMPT_PREFIXES) so the browser portal —
# which can't attach an X-Firebase-AppCheck header — can send OTP / verify /
# refresh / logout, while mobile keeps /api/v1/auth/* App-Check-enforced.
app.include_router(auth_router, prefix="/api/portal")
# Self-serve company signup lives in the same App-Check-exempt portal
# namespace: POST /api/portal/companies/signup. Authenticated-first (work-email
# OTP session) — see routes/corporate_signup.py.
app.include_router(corporate_signup_router, prefix="/api/portal")

# WebSocket routes — mounted at root so the path /ws/{client_type}/{client_id} is served directly
app.include_router(websocket_router)

# Optional MCP server at root (/mcp): same AI tool registry over streamable
# HTTP for external agent clients. Not mounted when the `mcp` SDK is absent;
# requests are 503 until app_settings.ai_mcp_enabled is turned on.
from ai.mcp_server import build_mcp_asgi_app  # noqa: E402

_mcp_asgi_app = build_mcp_asgi_app()
if _mcp_asgi_app is not None:
    app.mount("/mcp", _mcp_asgi_app)

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
# See the ordering note above v1_api_router's equivalent block: the static
# single-segment routers must be included before corporate_accounts_router's
# catch-all GET /{account_id}.
app.include_router(corporate_wallet_router, prefix="/api", dependencies=[Depends(require_module("corporate_accounts"))])
app.include_router(
    corporate_subscriptions_router, prefix="/api", dependencies=[Depends(require_module("corporate_accounts"))]
)
app.include_router(
    corporate_accounts_router, prefix="/api", dependencies=[Depends(require_module("corporate_accounts"))]
)
# Corporate member/allowance/domain endpoints served at root (`/company/{id}/...`)
# because the rider app calls /company/{id}/policy and /company/{id}/allowances
# without an /api prefix (verified in workProfileStore.ts). Do not remove until
# a coordinated mobile release migrates those calls to /api/company/{id}/...
app.include_router(corporate_company_router)
app.include_router(corporate_company_bookings_router)
app.include_router(corporate_company_kyb_router)
app.include_router(corporate_rider_router)
app.include_router(corporate_rider_router, prefix="/api/v1")
# /api mounts: the company portal (admin-dashboard) reaches the backend only
# through its /api/* Next.js proxy, so the company/rider routers must also
# answer there. Root mounts above stay for rider-app compat. Same doubled
# rate-limit caveat as the settings router double-mount.
app.include_router(corporate_company_router, prefix="/api")
app.include_router(corporate_company_bookings_router, prefix="/api")
app.include_router(corporate_company_kyb_router, prefix="/api")
app.include_router(corporate_rider_router, prefix="/api")
# files_router serves document files at /api/documents/{id} (used by admin dashboard).
# Also mounted under /api/v1 so legacy driver_documents rows whose document_url was
# written as /api/v1/documents/{id} by the old base64-in-DB upload path keep resolving.
app.include_router(files_router, prefix="/api")
app.include_router(files_router, prefix="/api/v1")

# Configure structured logging with Loguru
import sys  # noqa: E402

from loguru import logger  # noqa: E402

# Remove default handler and add custom JSON handler
logger.remove()

# PIPEDA runtime guard, installed BEFORE any sink so it covers all of them —
# the stderr JSON sink below and the loguru->Sentry bridge added further down.
# Redacts sensitive keys out of record["extra"] (which serialize=True emits as
# JSON, and which is where loguru puts keyword arguments) and PII values out of
# the rendered message. Every redaction is counted and the offending call site is
# named once, because the point is to fix the call site, not to launder it.
try:
    from utils.log_guard import install as _install_log_guard
except ImportError:  # pragma: no cover - dual import
    from .utils.log_guard import install as _install_log_guard  # type: ignore
_install_log_guard(logger)

logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}",
    serialize=True,  # This enables JSON formatting
    # PIPEDA: loguru defaults to diagnose=True, which annotates every traceback
    # frame with the *values* of the locals and call arguments in scope. On a
    # settlement failure that prints things like
    #     settle_fare("rider@example.com", Decimal("42.50"), "+13065551234")
    #     └ <rider row with phone/email/address>
    # straight into the log stream. utils/log_guard.py cannot catch it: the
    # guard scrubs record["message"] and record["extra"], but the annotated
    # frames are rendered by the sink from record["exception"], downstream of
    # both. backtrace=True is kept — the stack itself is what makes an error
    # actionable; it is the variable values that must not be logged.
    backtrace=True,
    diagnose=False,
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
        LoggingIntegration(event_level="ERROR", level="WARNING"),
    ]
    if _StarletteMiddleware is not None:
        integrations.append(_StarletteMiddleware())

    # C1: defense-in-depth PII scrubber. The loguru->Sentry bridge below forwards
    # arbitrary error-message strings to a third party; these hooks redact
    # phones/emails/coords/postal codes from event + breadcrumb text and stamp
    # surface=backend before egress. They never drop an event on failure.
    from utils.sentry_scrub import pipeda_sentry_options, tags_from_log_extra

    # The PIPEDA-critical options (send_default_pii, include_local_variables,
    # before_send, before_breadcrumb) come from pipeda_sentry_options() so they are
    # unit-assertable — this init call only runs on import of this module, so
    # inlining them left them untestable. Keep them last: they must not be
    # overridable by the operational options above.
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=integrations,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=settings.ENV if hasattr(settings, "ENV") else "production",
        **pipeda_sentry_options(),
    )

    # Bridge loguru → Sentry. The LoggingIntegration above only captures
    # stdlib `logging` records; the rest of the backend uses loguru and
    # would otherwise be invisible in Sentry (including the high-signal
    # REFRESH TOKEN REUSE DETECTED alert in utils/refresh_tokens.py).
    #
    # Promote the loguru `extra={...}` context (domain/ride_id/driver_id/...)
    # onto a forked Sentry scope so events are triageable by domain/surface and
    # correlatable — without this they arrived with only `environment` set.
    def _loguru_sentry_sink(message: "Any") -> None:  # noqa: ANN401, F821
        record = message.record
        tags = tags_from_log_extra(record.get("extra") or {})
        # sentry-sdk 2.x prefers new_scope(); fall back to push_scope() on 1.x.
        scope_cm = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with scope_cm() as scope:
            for key, val in tags.items():
                scope.set_tag(key, val)
            exc_info = record["exception"]
            if exc_info is not None and exc_info.value is not None:
                sentry_sdk.capture_exception(exc_info.value)
            else:
                sentry_sdk.capture_message(record["message"], level="error")

    logger.add(_loguru_sentry_sink, level="ERROR")
    logger.info("Sentry SDK initialized for error monitoring")
    # One low-volume boot event per process: positively confirms the
    # DSN→Sentry pipeline works (and completes the Fly Sentry extension's
    # "waiting for first event" setup check) instead of waiting for the
    # first real production error to find out the integration is broken.
    # (Merge note: main added an unconditional capture_message here; this
    # branch's production-gated version supersedes it — dev boots stay quiet.)
    if getattr(settings, "ENV", "development") == "production":
        sentry_sdk.capture_message("spinr backend started — Sentry pipeline verified", level="info")
elif getattr(settings, "ENV", "development") == "production":
    # Same precedent as the Redis-missing check (L-P1-1): observability
    # degradation must not take the API down, but it must be impossible to
    # miss. A production replica without Sentry means payment/dispatch/auth
    # errors go nowhere — set the SENTRY_DSN secret (Fly: Sentry extension
    # "Deploy Secrets", or `fly secrets set SENTRY_DSN=...`).
    logger.error(
        "SENTRY_DSN is not set in production — backend errors are NOT being "
        "reported to Sentry. Deploy the SENTRY_DSN secret to restore error tracking."
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104
