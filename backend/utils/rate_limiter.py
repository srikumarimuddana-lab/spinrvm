"""
Rate limiting utilities for Spinr API.

This module provides configurable rate limiting with support for:
- IP-based limiting
- User-based limiting
- Per-endpoint limits
- Redis-backed distributed limiting (for production)
"""

import hashlib
import os
import time
from functools import wraps
from typing import Callable, Dict

import jwt
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from limits.aio.storage import MemoryStorage
from loguru import logger
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_ipaddr

try:
    from core.config import settings
    from utils.async_limiter import AsyncLimiter
    from utils.metrics import inc as _metric_inc
except ImportError:  # pragma: no cover — package-relative fallback for tests
    from ..core.config import settings  # type: ignore[no-redef]
    from .async_limiter import AsyncLimiter  # type: ignore[no-redef]
    from .metrics import inc as _metric_inc  # type: ignore[no-redef]

# ============================================================================
# Rate Limiter Configuration
# ============================================================================

# Storage backend: redis:// when RATE_LIMIT_REDIS_URL is set, otherwise
# "memory://" (process-local; dev only). In production the empty default
# is blocked by _validate_production_config() so we never silently fall
# back to memory across a multi-machine deploy.
_rate_limit_storage_uri = os.environ.get("RATE_LIMIT_REDIS_URL") or settings.RATE_LIMIT_REDIS_URL or "memory://"

if _rate_limit_storage_uri == "memory://":
    logger.warning(
        "Rate limiter using in-process 'memory://' storage — counters are "
        "per-worker and will NOT rate-limit correctly across multiple "
        "replicas. Set RATE_LIMIT_REDIS_URL for production deployments."
    )
else:
    scheme = _rate_limit_storage_uri.split("://", 1)[0]
    logger.info(f"Rate limiter configured with async distributed storage: {scheme}://…")

# ---------------------------------------------------------------------------
# OTP fail-closed policy
# ---------------------------------------------------------------------------
# For keys that identify OTP flows ("otp", "send_otp", "verify_otp"), the
# in-memory fallback is NOT acceptable: on a multi-replica deployment each
# replica keeps its own counter, so the effective limit becomes (limit ×
# N_replicas)/window — making brute-force trivially easy.
#
# If Redis is unavailable at request time for an OTP key we therefore
# raise HTTP 503 rather than silently degrade.  Non-OTP keys continue to
# use the in-memory fallback because the risk is much lower (general API
# rate limiting, not auth security).
# ---------------------------------------------------------------------------
_OTP_KEY_FRAGMENTS = ("otp", "send_otp", "verify_otp")


def _is_otp_key(key: str) -> bool:
    """Return True if *key* belongs to an OTP rate-limit bucket."""
    lower = key.lower()
    return any(fragment in lower for fragment in _OTP_KEY_FRAGMENTS)


def _is_security_scope(scope: str) -> bool:
    normalized = scope.lower()
    return _is_otp_key(normalized) or "/auth/" in normalized


def _record_storage_error(scope: str, error: Exception, fail_closed: bool) -> None:
    policy = "fail_closed" if fail_closed else "fallback"
    logger.opt(exception=error).error(f"Async rate-limit storage failed; policy={policy}; scope={scope}")
    _metric_inc("spinr_rate_limit_storage_errors_total", {"policy": policy})


def get_real_client_ip(request: Request) -> str:
    """Resolve the true client IP behind the CDN/proxy chain (C5).

    slowapi's ``get_ipaddr`` trusts the LEFTMOST ``X-Forwarded-For`` entry,
    which is fully client-supplied and therefore spoofable — a forged header
    lets an attacker rotate the rate-limit key at will. Spinr sits behind
    Cloudflare, which OVERWRITES ``CF-Connecting-IP`` with the real connecting
    IP and ignores any client-supplied value, so it is authoritative. Prefer it,
    then ``X-Real-IP`` (set by the platform edge), then fall back to
    ``get_ipaddr`` for local dev / non-Cloudflare paths.

    (Origin hosts must only accept traffic from Cloudflare for this to be
    airtight against a direct-to-origin bypass — an infra/network control.)
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return get_ipaddr(request)


def _extract_unverified_user_id(request: Request) -> str | None:
    """Best-effort user id from the bearer token, signature NOT verified.

    Mirrors `core/middleware.py::_extract_user_id` (used there for log
    correlation only) — safe for rate-limit *keying* because it never grants
    authorization: the same request still has to pass the real,
    signature-verified `get_current_user` dependency before any handler code
    runs, so a forged/garbage token can only ever land in a throwaway bucket
    for a request that then 401s, not impersonate another user's bucket.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth[len("Bearer ") :], options={"verify_signature": False})
    except Exception:
        return None
    uid = payload.get("user_id") or payload.get("sub")
    return str(uid) if uid is not None else None


def _metric_path_label(request: Request) -> str:
    """Bounded path label for metrics — the route TEMPLATE, not the live URL.

    ``request.url.path`` embeds ride and user UUIDs, so labelling a counter with
    it creates one permanent label set per entity. ``utils/metrics.py`` has no
    eviction, so that map only grows — fastest during exactly the burst these
    metrics exist to diagnose — and the capacity watchdog now scans it once a
    minute per replica. It also makes the counter useless for triage: thousands
    of one-hit rows instead of a rate per endpoint.

    Starlette stores the matched route on the request scope, whose ``path`` is
    the template (``/rides/{ride_id}/cancel``), giving one label per endpoint.

    Falls back to a literal ``"unmatched"`` rather than the raw path when no
    route matched (404s, and some middleware-level rejections) — an unmatched
    request is attacker-controlled input, and echoing it into a label is the
    same unbounded-cardinality problem with a hostile author.

    Defensive by design: this runs inside the 429 exception handler, so raising
    here would turn a rate-limit response into a 500. Any unexpected request
    shape degrades to the fallback label instead of propagating.
    """
    try:
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        if isinstance(template, str) and template:
            return template
    except (AttributeError, TypeError) as exc:
        # Narrow on purpose: only a missing/odd `scope` can land here. Debug
        # rather than warning — this is a label-quality degradation on a request
        # that is already being rejected, not a failure worth paging on. It is
        # logged rather than swallowed so an unexpected request shape is
        # discoverable instead of silently becoming "unmatched" forever.
        logger.debug(f"rate-limit metric label fell back to 'unmatched': {exc}")
    return "unmatched"


def get_user_or_ip_key(request: Request) -> str:
    """Key rate limiting by authenticated user, falling back to IP.

    Mobile carriers put hundreds of subscribers behind one carrier-grade NAT
    egress IP. Under IP keying every rider on a given carrier in a given city
    shares ONE bucket, so a burst of legitimate users 429s itself: 5 bookings
    per minute across an entire carrier, and — worse — an SOS that can be
    refused because unrelated strangers on the same egress IP happened to tap
    ride actions. That is a correctness bug on a safety surface, not a tuning
    nit.

    Keying on the user makes the limit mean what its name says: N per minute
    *per user*. It is also strictly harder to evade than IP keying, which an
    abuser defeats for free by rotating through a proxy pool.

    Safety of the unverified decode: see `_extract_unverified_user_id`. On
    these routes `Depends(get_current_user)` resolves BEFORE the limiter-wrapped
    handler body, so a forged token 401s regardless — the worst a bad token can
    do is land in a throwaway bucket for a request that then fails auth.

    Anonymous requests (no bearer token) keep IP keying, which is the only
    identity available for them.

    Kill switch: set ``RATE_LIMIT_USER_KEYING=off`` to revert to pure IP keying
    without a code deploy (``fly secrets set`` rolls machines with the new
    value). See docs/runbooks/capacity-scaling.md §3.
    """
    if os.environ.get("RATE_LIMIT_USER_KEYING", "on").strip().lower() in ("off", "0", "false", "no"):
        return f"ip:{get_real_client_ip(request)}"
    user_id = _extract_unverified_user_id(request)
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_real_client_ip(request)}"


def get_ai_chat_key(request: Request) -> str:
    """Key AI-chat rate limiting by user, not IP (ACTION_ITEMS.md AI1).

    An IP-keyed limit is defeated for free by rotating source IPs (cheap via
    any VPN/proxy pool), which removes the per-minute ceiling on LLM spend
    for a single account. Falls back to IP only when no bearer token is
    present (the request will 401 downstream regardless).
    """
    user_id = _extract_unverified_user_id(request)
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_real_client_ip(request)}"


# Default limiter — keyed on the authoritative client IP (CF-Connecting-IP when
# behind Cloudflare) instead of the spoofable leftmost X-Forwarded-For. (P2-7, C5)
default_limiter = AsyncLimiter(
    key_func=get_real_client_ip,
    default_limits=["100/minute", "1000/hour"],
    storage_uri=_rate_limit_storage_uri,
    fallback_storage=MemoryStorage(),
    fail_closed_predicate=_is_security_scope,
    on_storage_error=_record_storage_error,
)

# ============================================================================
# Custom Key Functions
# ============================================================================


def get_client_identifier(request: Request) -> str:
    """
    Get a unique client identifier combining IP and user info.

    Priority:
    1. User ID from auth (if authenticated)
    2. Phone number from request (for OTP endpoints)
    3. IP address (fallback)
    """
    # Try to get user ID from request state (set by auth middleware)
    if hasattr(request.state, "user") and request.state.user:
        user_id = request.state.user.get("id")
        if user_id:
            return f"user:{user_id}"

    # Try to get phone from request body (for OTP requests)
    try:
        pass
        # Note: This is a best-effort attempt, body may already be consumed
        # For actual phone-based limiting, apply decorator directly with phone param
    except Exception:  # noqa: S110
        logger.opt(exception=True).warning("rate_limiter: get_rate_limit_key: body parse failed; falling back to IP")

    # Fallback to the authoritative client IP (CF-Connecting-IP when present).
    return f"ip:{get_real_client_ip(request)}"


def get_phone_based_key(request: Request) -> str:
    """Get rate limit key based on phone number for OTP endpoints."""
    # Try to extract phone from path or query params
    phone = request.path_params.get("phone") or request.query_params.get("phone")
    if phone:
        # Hash the phone for privacy in logs
        phone_hash = hashlib.sha256(phone.encode()).hexdigest()[:16]
        return f"phone:{phone_hash}"

    # Fallback to the authoritative client IP (CF-Connecting-IP when present).
    return f"ip:{get_real_client_ip(request)}"


def get_company_booking_key(request: Request) -> str:
    """Key company guest-booking rate limiting by company, not raw IP.

    Corporate + admin portal review, gap #41: an IP-keyed limit is
    defeated by rotating source IPs (cheap via any VPN/proxy pool) — the
    caller must already be an authenticated, active member of company_id
    (require_company_member runs before this limit is checked, since it's
    a route dependency, not the decorator's own concern), so scoping to
    company_id closes the free-IP-rotation SMS-bomb bypass without
    needing a second DB round-trip inside the key function itself.
    company_id is read from the URL path, which every route this limiter
    is applied to (/company/{company_id}/bookings) always has — the IP
    fallback below only matters if this limiter is ever reused on a route
    without that path param.
    """
    company_id = request.path_params.get("company_id")
    if company_id:
        return f"company_booking:{company_id}"
    return f"ip:{get_real_client_ip(request)}"


# ============================================================================
# Rate Limit Decorators
# ============================================================================


def rate_limit_auth(requests: int = 5, period: int = 60, key_func: Callable = get_client_identifier):
    """
    Rate limit decorator for authentication endpoints.

    Args:
        requests: Number of allowed requests
        period: Time period in seconds
        key_func: Function to extract the rate limit key
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # The actual rate limiting is handled by SlowAPI
            # This wrapper adds logging and custom error handling
            try:
                return await func(*args, **kwargs)
            except RateLimitExceeded:
                logger.warning(f"Rate limit exceeded for {key_func.__name__}: {requests} requests per {period}s")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Too many requests. Please wait {period} seconds before trying again.",
                        "retry_after": period,
                        "limit": requests,
                        "period": period,
                    },
                    headers={
                        "Retry-After": str(period),
                        "X-RateLimit-Limit": str(requests),
                        "X-RateLimit-Remaining": "0",
                    },
                ) from None

        return wrapper

    return decorator


# ============================================================================
# Pre-configured Rate Limiters for Specific Endpoints
# ============================================================================

# OTP endpoints - very restrictive to prevent abuse
otp_rate_limit = default_limiter.limit("3/minute")

# Login endpoints - moderately restrictive
login_rate_limit = default_limiter.limit("5/minute")

# General API endpoints - more permissive. Keyed per user (get_user_or_ip_key),
# not per IP: carrier-grade NAT put a whole carrier's riders in one bucket.
# The limit is unchanged because 30/minute is generous *per user* — and the
# limiter scope includes the URL path, so each route gets its own bucket.
api_rate_limit = default_limiter.limit("30/minute", key_func=get_user_or_ip_key)

# Ride creation - prevent spam ride requests (max 5 per minute per user)
ride_request_limit = default_limiter.limit("5/minute", key_func=get_user_or_ip_key)

# Ride cancellation - max 10 per hour per user (prevents cancellation farming)
cancel_ride_limit = default_limiter.limit("10/hour", key_func=get_user_or_ip_key)

# Ride read endpoints — generous ceiling covers 3 s polling without churn.
# Per-user keyed: under IP keying a carrier NAT's riders shared this bucket, so
# ~6 simultaneously-polling riders on one carrier exhausted it between them.
ride_read_limit = default_limiter.limit("120/minute", key_func=get_user_or_ip_key)

# Corporate guest bookings: each one fires 2-3 customer SMS, so this is an
# SMS-cost/abuse bound as much as a booking bound. 30/hour comfortably covers
# a busy showroom desk. (The /company + /api/company double-mount tracks
# each prefix separately — accepted caveat, see server.py.) Keyed by
# company_id (get_company_booking_key), not IP — see gap #41: IP-only
# keying let a caller rotate source IPs to bypass the cap entirely.
company_booking_limit = default_limiter.limit("30/hour", key_func=get_company_booking_key)

# Promo enumeration guard - max 20 per minute
promo_available_limit = default_limiter.limit("20/minute")

# Promo brute-force guard - max 10 per minute
promo_validate_limit = default_limiter.limit("10/minute")

# Location updates - allow frequent updates for drivers. Applied to
# POST /drivers/location-batch (routes/drivers/location.py). This limiter was
# defined but decorated NOTHING until 2026-08-07, leaving the driver GPS
# ingestion path entirely unlimited. Per-user keyed so one runaway device
# cannot spend the budget of other drivers behind the same carrier NAT.
location_update_limit = default_limiter.limit("60/minute", key_func=get_user_or_ip_key)

# Payment actions (tip, process-payment) — sensitive financial ops, tight limit
payment_action_limit = default_limiter.limit("5/minute", key_func=get_user_or_ip_key)

# Ride rating — once per completed ride, extra friction prevents spam
ride_rating_limit = default_limiter.limit("5/hour", key_func=get_user_or_ip_key)

# Data export (DSAR) — each call fans out 6 DB reads, builds a ZIP, uploads to
# Storage, and sends an email. Tight cap prevents storage fill / SES exhaustion.
dsar_export_limit = default_limiter.limit("3/hour")

# Admin Data Transfer export — full-fidelity, unredacted, up to 100
# entities/call (profile + documents + ride history + insurance periods
# each). Unlike dsar_export_limit (a driver exporting only their own data),
# this exports OTHER users' PII at an admin's discretion — a compromised or
# malicious admin session could otherwise issue export after export to
# exfiltrate data quickly. Backgrounded (see data_transfer_export.py), so
# this isn't guarding request-thread exhaustion, it's bounding total
# export volume per admin-facing client over time (cf. dsar_export_limit).
data_transfer_export_limit = default_limiter.limit("10/hour")

# Admin Data Transfer import — /validate is a read-only dry-run (parse +
# report, no writes); /commit creates users/drivers rows and, with
# update_existing=true, mutates already-imported ones. commit is the
# write path and gets the tighter limit — a compromised or scripted admin
# session should not be able to mass-create/mutate accounts unbounded.
data_transfer_import_validate_limit = default_limiter.limit("30/hour")
data_transfer_import_commit_limit = default_limiter.limit("10/hour")

# Admin legacy booking import — /validate is a read-only dry-run over four
# CSVs; /commit inserts rides plus offsetting payouts and recounts
# drivers.total_rides. commit is the write path into the two tables that feed
# driver payable balance, so it gets the tighter limit. Both are looser on
# parse cost than they look: the work runs in a worker thread, and the import
# is a one-time operation run a handful of times at most.
booking_import_validate_limit = default_limiter.limit("30/hour")
booking_import_commit_limit = default_limiter.limit("10/hour")

# Admin driver-import (CSV) — /validate is a read-only dry-run (parse +
# report, no writes); /commit creates user + driver rows. Same shape as
# data_transfer_import/booking_import above: commit is the write path and
# gets the tighter limit so a compromised or scripted admin session can't
# mass-create driver accounts unbounded. Corporate + admin portal review,
# gap #45 — this endpoint previously had no rate limit at all.
driver_import_commit_limit = default_limiter.limit("10/hour")

# Admin Data Transfer jobs (list/detail/download-link) — read-only status
# polling, but download-link regeneration mints a fresh signed Storage URL
# each call; bound it the same as other admin list/detail endpoints.
data_transfer_jobs_limit = default_limiter.limit("60/minute")

# Admin Data Transfer search — read-only, but runs a count_documents
# head-count query per call; same order of magnitude as other admin
# search/autocomplete endpoints (cf. admin_places_autocomplete's 60/minute
# in routes/admin/rides.py).
data_transfer_search_limit = default_limiter.limit("60/minute")

# Admin export-approval gate (ACTION_ITEMS.md B10) — listing the pending
# queue is read-only/cheap (60/minute, matches data_transfer_jobs_limit);
# approve/deny are decisions a human makes rarely, tightened to discourage
# scripted rubber-stamping.
export_approvals_list_limit = default_limiter.limit("60/minute")
export_approvals_decide_limit = default_limiter.limit("20/minute")

# Tax-document email (T4A PDF / earnings CSV) — each call reads up to 10k rides,
# renders/builds a document, and sends an email to the driver. Cap prevents
# inbox-bombing + SES quota / sender-reputation abuse (cf. dsar_export_limit).
# Applied per-endpoint, so this allows 6 T4A + 6 CSV sends/hour with headroom
# for retries.
tax_doc_email_limit = default_limiter.limit("6/hour")

# Admin driver earnings-statement endpoints. Both rebuild the statement from
# live data (rides + bonuses + incentives + payouts reads) and render a PDF, so
# neither is free; the email path additionally sends to the DRIVER's inbox at an
# admin's discretion, which is the abuse surface tax_doc_email_limit guards for
# the driver's own self-serve sends. Email is the tighter of the two: a support
# agent working a queue needs a handful per hour, while a compromised admin
# session must not be able to inbox-bomb a driver or burn SES reputation.
# Download is looser (no outbound mail, and an admin may legitimately pull
# several periods for one dispute) but still bounded, unlike before.
admin_statement_email_limit = default_limiter.limit("20/hour")
admin_statement_download_limit = default_limiter.limit("60/hour")

# AI assistant chat — each message triggers LLM spend; per-user daily cap
# (ai_daily_message_cap) is enforced separately in backend/ai/orchestrator.py.
# Keyed by user (ACTION_ITEMS.md AI1), not IP — an IP-keyed limit is defeated
# by rotating source IPs, multiplying one account's effective per-minute
# budget by however many IPs it rotates through.
ai_chat_limit = default_limiter.limit("10/minute", key_func=get_ai_chat_key)

# In-ride messaging — generous but bounded to prevent SMS relay abuse
ride_message_limit = default_limiter.limit("30/minute", key_func=get_user_or_ip_key)

# Ride state transitions (start, complete, emergency) — ride lifecycle ops.
# Per-user keyed, and this one is a safety fix as much as a capacity one: it
# guards POST /rides/{id}/emergency (routes/rides/safety.py:38), so under IP
# keying an SOS could be refused because unrelated strangers behind the same
# carrier NAT had spent the bucket on ordinary ride actions. Note the SOS route
# uses get_current_user_allow_expired; _extract_unverified_user_id ignores
# expiry too, so an expired-but-valid token still keys to its real user.
ride_action_limit = default_limiter.limit("20/minute", key_func=get_user_or_ip_key)

# Document uploads - restrictive to prevent abuse
document_upload_limit = default_limiter.limit("5/minute")

# Admin endpoints - restrictive for security
admin_rate_limit = default_limiter.limit("100/minute")

# Admin wallet mutations — additional friction against accidental bulk credit/debit (F-36)
admin_wallet_limit = default_limiter.limit("10/minute")

# Admin mass notifications — prevent accidental spam blasts (F-36)
admin_mass_notify_limit = default_limiter.limit("3/minute")

# Admin staff deletion — one-way destructive action, extra caution (F-36)
admin_staff_delete_limit = default_limiter.limit("5/minute")

# Admin AI reply-suggestion (Help Desk) — each call hits a paid LLM with a
# third-party quota; cap per-IP to stop budget/quota exhaustion by an agent.
admin_ai_suggest_limit = default_limiter.limit("20/minute")

# Admin AI console (routes/admin/ai_console.py, super-admin-only + audited) —
# each turn runs the same paid-LLM orchestrator path as the rider-facing
# /ai/chat (ai_chat_limit, 10/minute), and the orchestrator deliberately
# EXEMPTS admin-console turns from the impersonated user's daily message cap
# (backend/ai/orchestrator.py `_over_daily_cap`, gated on
# `admin_actor_id is None`) so heavy console testing doesn't drain the
# target rider/driver's quota. That exemption removes the one ceiling that
# would otherwise bound LLM spend on this path, so the endpoint needs its
# own limit as a defensive ceiling — not because the caller is untrusted
# (super_admin JWTs are fully trusted and every call is audit-logged), but
# against a compromised/malicious admin session or a runaway automation
# script hammering the endpoint. Matches admin_ai_suggest_limit's value:
# same class of endpoint (admin-only, paid-LLM-backed), and looser than the
# rider-facing 10/minute cap is appropriate given the lower risk profile
# (ACTION_ITEMS.md AI12).
admin_ai_console_limit = default_limiter.limit("20/minute")

# Admin SIN reveal/update (ACTION_ITEMS.md D8) — both are super_admin-gated and
# audit-logged before this limiter ever runs, so this is defense-in-depth, not
# closing an active exploit: a compromised or scripted super_admin session
# should still not be able to walk every driver's SIN unbounded, or churn a
# driver's SIN repeatedly without the friction of hitting a wall. 10/hour is
# D8's own suggested figure — generous for the legitimate case (T4A season
# support tickets run in the single digits per admin per day) while bounding
# a runaway/compromised session to a two-digit number of SINs per hour rather
# than an unbounded loop. Keyed per-admin (get_user_or_ip_key), not per-IP —
# every other admin_* limiter in this file defaults to IP keying, but SIN
# reveal/update is scoped to *an admin's* exposure, and IP keying would let
# multiple super_admins sharing one office/VPN egress IP silently share (and
# exhaust) one bucket, or would under-count a single admin who rotates IPs.
# admin JWTs carry a `user_id` claim (routes/admin/auth.py
# `_mint_admin_access_token`), so the existing get_user_or_ip_key key
# function keys correctly here with no new key function needed.
admin_sin_reveal_limit = default_limiter.limit("10/hour", key_func=get_user_or_ip_key)
admin_sin_update_limit = default_limiter.limit("10/hour", key_func=get_user_or_ip_key)

# Admin tax-ID bulk import (ACTION_ITEMS.md D8) — SIN-touching bulk operation,
# same super_admin + audit posture as reveal/update-sin above. /validate is a
# read-only dry-run (parse + report, no writes); /commit writes up to
# MAX_ROWS (500) SINs/GST BNs per call, so commit gets the tighter limit —
# same validate/commit asymmetry as data_transfer_import_*_limit,
# booking_import_*_limit, and driver_import_commit_limit above. Looser than
# the single-driver reveal/update limit (10/hour) because one call here is a
# deliberate one-time bulk migration op covering many drivers at once, not a
# per-driver action — the per-call MAX_ROWS cap already bounds blast radius
# per call, so the per-hour call cap only needs to guard against unbounded
# scripted looping, not single-driver granularity. Keyed per-admin like the
# SIN limiters above, for the same reason (this endpoint decrypts/writes
# SINs, not general admin CRUD where IP keying is the existing convention).
tax_id_import_validate_limit = default_limiter.limit("30/hour", key_func=get_user_or_ip_key)
tax_id_import_commit_limit = default_limiter.limit("10/hour", key_func=get_user_or_ip_key)


# ============================================================================
# Rate Limit Exceeded Handler
# ============================================================================


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom handler for rate limit exceeded errors.

    Emits the response shape pinned by docs/runbooks/rate-limits.md:

      Headers:
        Retry-After: <seconds>           — RFC 9110, integer seconds
        RateLimit-Limit: <amount>        — IETF draft-ietf-httpapi-ratelimit-headers
        RateLimit-Remaining: 0           — always 0 once we're past the limit
        RateLimit-Reset: <seconds>       — same as Retry-After (delta-seconds form)

      Body:
        {
          "error": "rate_limit_exceeded",
          "message": "...",
          "retry_after": <seconds>,
          "limit": <amount> | null,
          "documentation_url": "..."
        }

    The previous implementation hard-coded ``Retry-After: 60`` as a
    sentinel. The 429 fired correctly, but a client looking at the
    header to decide "wait 60s before retrying" was always told 60s
    regardless of whether the actual limit was 5/minute or 5/hour.
    We now read the limit's window size from ``exc.limit.limit`` —
    a worst-case wait that's guaranteed correct (the bucket will
    have headroom by then). Computing the *exact* bucket reset time
    requires probing the storage backend's per-key state, which the
    slowapi/limits abstraction doesn't expose cheaply; window-size
    is the standard fallback and what most rate-limit middleware
    use as Retry-After.
    """
    retry_after = 60  # safe default if exc.limit is malformed
    limit_amount: int | None = None
    try:
        # exc.limit is a slowapi.wrappers.Limit; the parsed RateLimitItem
        # is exc.limit.limit (yes, doubly nested — slowapi naming).
        rl_item = exc.limit.limit
        retry_after = int(rl_item.get_expiry())
        limit_amount = int(rl_item.amount)
    except (AttributeError, TypeError, ValueError) as e:
        # Never crash the handler — emitting a 429 with a sentinel
        # Retry-After is strictly better than 500'ing a rate-limited
        # request. Log loudly so we notice if slowapi changes shape.
        logger.warning(f"rate_limit_handler: could not derive retry_after/limit ({e})")

    headers: Dict[str, str] = {"Retry-After": str(retry_after)}
    if limit_amount is not None:
        # IETF draft-ietf-httpapi-ratelimit-headers (in last call as of
        # 2026). Even if the draft never RFCs, GitHub/Twitter/Stripe
        # already emit these and our clients' parsing logic is the
        # de-facto consumer. RateLimit-Reset uses the delta-seconds
        # form (same value as Retry-After) per the draft's §5.3.
        headers["RateLimit-Limit"] = str(limit_amount)
        headers["RateLimit-Remaining"] = "0"
        headers["RateLimit-Reset"] = str(retry_after)

    # Log the same IP the limiter keys on (CF-Connecting-IP behind Cloudflare),
    # not the raw socket peer — behind Fly/Railway the peer is the platform
    # proxy's private address, which misleads triage into thinking all clients
    # share one bucket.
    logger.warning(
        f"Rate limit exceeded | "
        f"Path: {request.url.path} | "
        f"Method: {request.method} | "
        f"IP: {get_real_client_ip(request)} | "
        f"Limit: {limit_amount} | "
        f"Retry-After: {retry_after}s"
    )
    _metric_inc("spinr_rate_limit_violation_total", {"path": _metric_path_label(request)})

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please slow down and try again later.",
            "retry_after": retry_after,
            "limit": limit_amount,
            "documentation_url": "https://spinr.app/docs/rate-limits",
        },
        headers=headers,
    )


# ============================================================================
# Sliding Window Rate Limiter (Redis-backed for production)
# ============================================================================


class RedisRateLimiter:
    """
    Redis-backed sliding window rate limiter for production use.

    This provides accurate rate limiting across multiple server instances.
    """

    def __init__(self, redis_url: str, default_limit: int = 100, window_seconds: int = 60):
        self.redis_url = redis_url
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._redis = None

    async def _get_redis(self):
        """Lazy Redis connection."""
        if self._redis is None:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(self.redis_url)
                await self._redis.ping()
                logger.info("Connected to Redis for rate limiting")
            except ImportError:
                logger.warning("Redis not available, falling back to memory-based limiting")
                self._redis = "memory"
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}, falling back to memory-based limiting")
                self._redis = "memory"
        return self._redis

    async def is_rate_limited(self, key: str, limit: int = None, window: int = None) -> tuple[bool, int]:
        """
        Check if a key is rate limited.

        Args:
            key: Unique identifier for the client
            limit: Maximum requests allowed (uses default if None)
            window: Time window in seconds (uses default if None)

        Returns:
            Tuple of (is_limited, remaining_requests)
        """
        limit = limit or self.default_limit
        window = window or self.window_seconds

        redis = await self._get_redis()

        if redis == "memory":
            # Fail-closed for OTP keys: in-memory fallback is unsafe on
            # multi-replica deployments because each replica tracks its own
            # counter, multiplying the effective limit by N_replicas.
            # See module-level comment on _OTP_KEY_FRAGMENTS for the rationale.
            if _is_otp_key(key):
                raise HTTPException(
                    status_code=503,
                    detail="Rate limiting unavailable, please retry",
                )
            # Non-OTP keys: in-memory fallback is acceptable for general rate limiting.
            return self._memory_check(key, limit, window)

        # Redis-based sliding window
        now = int(time.time())
        window_start = now - window
        key = f"ratelimit:{key}"

        pipe = redis.pipeline()
        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Count requests in window
        pipe.zcard(key)
        # Set expiry
        pipe.expire(key, window)

        try:
            results = await pipe.execute()
        except Exception as e:
            # Redis went down mid-operation — reset connection for next attempt.
            # Log at ERROR so this surfaces in SRE alerting (DV-6).
            logger.error(
                f"Redis unavailable mid-operation — rate limiter degraded to in-memory ({e}); "
                "OTP brute-force protection weakened on multi-replica deployments"
            )
            self._redis = None
            bare_key = key.replace("ratelimit:", "")
            # Fail-closed for OTP keys: do NOT fall back to in-memory — raise
            # 503 so the caller retries once Redis recovers.  See module-level
            # comment on _OTP_KEY_FRAGMENTS for the full rationale.
            if _is_otp_key(bare_key):
                raise HTTPException(
                    status_code=503,
                    detail="Rate limiting unavailable, please retry",
                ) from None
            return self._memory_check(bare_key, limit, window)

        current_count = results[2]

        if current_count > limit:
            return True, 0

        return False, limit - current_count

    def _memory_check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """In-memory fallback (not thread-safe, use only for development)."""
        # This is a simple implementation - in production use Redis
        if not hasattr(self, "_memory_store"):
            self._memory_store: Dict[str, list] = {}

        now = time.time()
        window_start = now - window

        # Clean old entries
        if key in self._memory_store:
            self._memory_store[key] = [t for t in self._memory_store[key] if t > window_start]
        else:
            self._memory_store[key] = []

        current_count = len(self._memory_store[key])

        if current_count >= limit:
            return True, 0

        # Record this request
        self._memory_store[key].append(now)

        return False, limit - current_count - 1


# ============================================================================
# Integration with FastAPI
# ============================================================================


def init_rate_limiting(app):
    """
    Initialize rate limiting for a FastAPI application.

    Args:
        app: FastAPI application instance
    """

    # Add the limiter to app state
    app.state.limiter = default_limiter

    # Add exception handler
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    logger.info("Rate limiting initialized")


# ============================================================================
# Usage Examples
# ============================================================================

"""
Example usage in route handlers:

from backend.utils.rate_limiter import (
    default_limiter,
    otp_rate_limit,
    login_rate_limit,
    api_rate_limit
)

@router.post("/otp/send")
@otp_rate_limit  # 3 requests per minute
async def send_otp(phone: str):
    ...

@router.post("/login")
@login_rate_limit  # 5 requests per minute
async def login(credentials: LoginCredentials):
    ...

@router.get("/users/me")
@api_rate_limit  # 30 requests per minute
async def get_current_user():
    ...

# Custom limit
@router.post("/rides")
@default_limiter.limit("10/minute")
async def create_ride(ride_data: RideData):
    ...
"""
