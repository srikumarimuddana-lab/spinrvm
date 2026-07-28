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
    logger.error(f"Async rate-limit storage failed; policy={policy}; scope={scope}", exc_info=error)
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
        logger.warning("rate_limiter: get_rate_limit_key: body parse failed; falling back to IP", exc_info=True)

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

# General API endpoints - more permissive
api_rate_limit = default_limiter.limit("30/minute")

# Ride creation - prevent spam ride requests (max 5 per minute per user)
ride_request_limit = default_limiter.limit("5/minute")

# Ride cancellation - max 10 per hour per user (prevents cancellation farming)
cancel_ride_limit = default_limiter.limit("10/hour")

# Ride read endpoints — generous ceiling covers 3 s polling without churn
ride_read_limit = default_limiter.limit("120/minute")

# Corporate guest bookings: each one fires 2-3 customer SMS, so this is an
# SMS-cost/abuse bound as much as a booking bound. 30/hour comfortably covers
# a busy showroom desk. (The /company + /api/company double-mount tracks
# each prefix separately — accepted caveat, see server.py.)
company_booking_limit = default_limiter.limit("30/hour")

# Promo enumeration guard - max 20 per minute
promo_available_limit = default_limiter.limit("20/minute")

# Promo brute-force guard - max 10 per minute
promo_validate_limit = default_limiter.limit("10/minute")

# Location updates - allow frequent updates for drivers
location_update_limit = default_limiter.limit("60/minute")

# Payment actions (tip, process-payment) — sensitive financial ops, tight limit
payment_action_limit = default_limiter.limit("5/minute")

# Ride rating — once per completed ride, extra friction prevents spam
ride_rating_limit = default_limiter.limit("5/hour")

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

# Admin Data Transfer jobs (list/detail/download-link) — read-only status
# polling, but download-link regeneration mints a fresh signed Storage URL
# each call; bound it the same as other admin list/detail endpoints.
data_transfer_jobs_limit = default_limiter.limit("60/minute")

# Admin Data Transfer search — read-only, but runs a count_documents
# head-count query per call; same order of magnitude as other admin
# search/autocomplete endpoints (cf. admin_places_autocomplete's 60/minute
# in routes/admin/rides.py).
data_transfer_search_limit = default_limiter.limit("60/minute")

# Tax-document email (T4A PDF / earnings CSV) — each call reads up to 10k rides,
# renders/builds a document, and sends an email to the driver. Cap prevents
# inbox-bombing + SES quota / sender-reputation abuse (cf. dsar_export_limit).
# Applied per-endpoint, so this allows 6 T4A + 6 CSV sends/hour with headroom
# for retries.
tax_doc_email_limit = default_limiter.limit("6/hour")

# AI assistant chat — each message triggers LLM spend; per-user daily cap
# (ai_daily_message_cap) is enforced separately in backend/ai/orchestrator.py
ai_chat_limit = default_limiter.limit("10/minute")

# In-ride messaging — generous but bounded to prevent SMS relay abuse
ride_message_limit = default_limiter.limit("30/minute")

# Ride state transitions (start, complete, emergency) — ride lifecycle ops
ride_action_limit = default_limiter.limit("20/minute")

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
