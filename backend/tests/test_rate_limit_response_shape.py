"""B-P1-8 — pin the 429 rate-limit response shape across all auth gates.

Two distinct 429 sources share one response contract so mobile clients
can use a single parser:

  1. slowapi window limit (e.g. /auth/logout @ 3/minute) handled by
     utils.rate_limiter.rate_limit_exceeded_handler.
  2. OTP brute-force lockout (/auth/send-otp + verify-otp) handled by
     a hand-rolled HTTPException in routes.auth._check_otp_lockout.

Both paths must emit:
  Headers:
    Retry-After: <seconds>           — RFC 9110 (integer seconds)
    RateLimit-Limit: <amount>        — IETF draft-ietf-httpapi-ratelimit-headers
    RateLimit-Remaining: 0
    RateLimit-Reset: <seconds>       — delta-seconds form

  Body (rate_limit_exceeded_handler only — the OTP path uses
  HTTPException's standard {detail} shape):
    error="rate_limit_exceeded", message=str, retry_after=int,
    limit=int|None, documentation_url=str

If either contract drifts, the shared/api/client.ts RateLimitError
parser will silently fall through to the generic Error path and
mobile users get "Request failed" instead of "Try again in 60s".
"""

from __future__ import annotations

import os
import sys

# test_p1_auth_hardening.py and test_csrf_middleware.py stub slowapi and
# utils.rate_limiter as MagicMocks at collection time.  Pop all of them so
# this file always imports the real slowapi and a real Limiter.
for _stale in ["slowapi", "slowapi.errors", "slowapi.util", "utils.rate_limiter"]:
    sys.modules.pop(_stale, None)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

# Pre-cache backend.utils.rate_limiter NOW (collection time), while
# os.environ still has JWT_SECRET etc. (set by conftest.py's setdefault).
# test_p1_auth_hardening.py's _make_settings() pops JWT_SECRET/SUPABASE_URL
# in its finally block before rate_limit tests run; importing rate_limiter
# after that fails because Settings() requires those vars.  Caching here
# avoids the re-import during test execution entirely.
#
# test_csrf_middleware.py stubs sys.modules["core.config"] = MagicMock at its
# own collection time (before this file).  Pop it temporarily so the fresh
# backend.utils.rate_limiter import uses the real Settings, then restore it.
_stale_core_cfg = sys.modules.pop("core.config", None)
try:
    import backend.utils.rate_limiter as _rate_limiter_mod  # noqa: F401  — side-effect: caches module
finally:
    if _stale_core_cfg is not None:
        sys.modules["core.config"] = _stale_core_cfg

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _app_with_auth_router() -> FastAPI:
    """Build a stripped FastAPI app that mounts only the auth router and
    wires the slowapi handler — bypasses server.py's full middleware
    stack (which pulls in multipart parsers we don't need here)."""
    import sys

    # test_p1_auth_hardening.py installs sys.modules["slowapi"] = _slowapi_mock
    # at collection time (after this file's collection-time pops run).  That
    # noop mock survives into test execution, so routes.auth's module-level
    # `limiter = Limiter(...)` would get a MagicMock Limiter whose .limit() is
    # a passthrough decorator — rate limiting never fires.
    #
    # Pop "slowapi" (the package) so routes.auth reimports a real Limiter.
    # Do NOT pop slowapi.errors or slowapi.extension: those modules' import-time
    # binding of RateLimitExceeded must stay the same class object that
    # slowapi.extension will raise — popping and reimporting creates a NEW class
    # that won't match the registered exception handler.
    for _stale in (
        "slowapi",
        "routes.auth",
        "backend.routes.auth",
        "utils.rate_limiter",
    ):
        sys.modules.pop(_stale, None)
    for _routes_pkg_key in ("backend.routes", "routes"):
        _pkg = sys.modules.get(_routes_pkg_key)
        if _pkg is not None:
            try:
                delattr(_pkg, "auth")
            except AttributeError:
                pass

    # conftest puts both backend/ and the project root on sys.path, so
    # both `from utils...` and `from backend.utils...` resolve. Use the
    # bare path to match the routes' own imports.
    from dependencies import get_current_user
    from routes.auth import api_router

    # backend.utils.rate_limiter was pre-cached at collection time (module-level
    # code above) while env vars were intact — no fresh import needed here.
    from backend.utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    # slowapi.extension captured RateLimitExceeded at its own import time.
    # By the time this test runs, some earlier test may have caused
    # slowapi.errors to be reimported, creating a NEW class object that
    # slowapi.extension doesn't know about.  Registering the handler with
    # the NEW class means the exception (raised with OLD class) won't match.
    # Fix: always pull the class from slowapi.extension directly — that is
    # the exact same object the limiter will raise.
    import slowapi.extension as _slowapi_ext
    _RateLimitExceeded = _slowapi_ext.RateLimitExceeded

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(_RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(api_router)

    async def fake_user():
        return {"id": "rider-x", "phone": "+15551234567"}

    app.dependency_overrides[get_current_user] = fake_user
    return app


# ─────────────────────────────────────────────────────────────────────────────
# slowapi 429 — /auth/logout (3/minute) is the canonical example
# ─────────────────────────────────────────────────────────────────────────────


class TestSlowapi429ResponseShape:
    """Pins the response shape for slowapi-tripped 429s. /auth/logout is
    rate-limited to 3/minute; the 4th call in the same window must
    return the documented headers + body."""

    def test_429_emits_retry_after_and_ratelimit_headers(self):
        app = _app_with_auth_router()
        client = TestClient(app)

        # Burn through the 3/minute budget
        for _ in range(3):
            client.post("/auth/logout", json={"refresh_token": "x"})
        r = client.post("/auth/logout", json={"refresh_token": "x"})

        assert r.status_code == 429

        # RFC 9110 Retry-After: integer seconds. Must equal the window
        # size (60 for "/minute") rather than the previous hardcoded
        # sentinel that always read "60" regardless of limit.
        assert r.headers.get("Retry-After") == "60"

        # IETF draft-ietf-httpapi-ratelimit-headers
        assert r.headers.get("RateLimit-Limit") == "3"
        assert r.headers.get("RateLimit-Remaining") == "0"
        assert r.headers.get("RateLimit-Reset") == "60"

    def test_429_body_carries_retry_after_and_limit_fields(self):
        """The body is the fallback for clients running through proxies
        that strip arbitrary headers (some corporate WAFs do exactly
        this). RateLimitError client-side falls back to body fields
        when headers are absent."""
        app = _app_with_auth_router()
        client = TestClient(app)
        for _ in range(3):
            client.post("/auth/logout", json={"refresh_token": "x"})
        r = client.post("/auth/logout", json={"refresh_token": "x"})

        assert r.status_code == 429
        body = r.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["retry_after"] == 60
        assert body["limit"] == 3
        # documentation_url stays present so the error log has a
        # crawlable pointer to the runbook.
        assert "documentation_url" in body

    @pytest.mark.asyncio
    async def test_retry_after_matches_window_for_per_hour_limit(self):
        """Sanity check that get_expiry() actually scales by window —
        a 2/hour limit must produce Retry-After: 3600, not 60.

        We unit-test the handler against a synthesised exception
        rather than a live route. A live route would need a
        decorated FastAPI endpoint, and slowapi's wrapper interacts
        with FastAPI's signature introspection in a way that flags
        the synthetic ``Request`` parameter as a missing query
        param (422). Calling the handler directly avoids that maze
        and tests exactly the contract we care about: Retry-After
        comes from ``exc.limit.limit.get_expiry()``."""
        from unittest.mock import MagicMock

        from fastapi import Request
        from limits import parse as parse_limit

        from utils.rate_limiter import rate_limit_exceeded_handler

        # Build a real RateLimitExceeded with a real "2/hour" limit
        # under exc.limit.limit so the handler exercises the same
        # attribute walk it does in production.
        rl_item = parse_limit("2 per 1 hour")
        fake_exc = RateLimitExceeded(MagicMock(limit=rl_item))

        # Minimal Request-shaped object — handler only reads
        # url.path, method, and the get_remote_address result.
        request = MagicMock(spec=Request)
        request.url.path = "/probe"
        request.method = "POST"
        request.client = MagicMock(host="127.0.0.1")
        request.headers = {}

        response = await rate_limit_exceeded_handler(request, fake_exc)

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "3600"
        assert response.headers["RateLimit-Limit"] == "2"
        assert response.headers["RateLimit-Remaining"] == "0"
        assert response.headers["RateLimit-Reset"] == "3600"


# ─────────────────────────────────────────────────────────────────────────────
# OTP lockout 429 — separate handler, must mirror the same shape
# ─────────────────────────────────────────────────────────────────────────────


class TestOtpLockout429ResponseShape:
    """Pins the OTP lockout 429 contract. _check_otp_lockout raises
    HTTPException(429) directly (it doesn't go through slowapi), so its
    headers are independent of the rate_limit_exceeded_handler. Both
    paths must emit the same header set so the client parser is
    single-shape."""

    def test_otp_lockout_emits_retry_after_and_ratelimit_headers(self):
        # Force ENV != production so dev OTP bypass is irrelevant; the
        # lockout check runs ahead of OTP validation either way.
        os.environ.setdefault("ENV", "test")

        from unittest.mock import AsyncMock, patch

        from fastapi import HTTPException

        # Import via the fully-qualified path so test_csrf_middleware.py's
        # bare-path 'core.config' stub (if still in sys.modules) does not
        # shadow the real Settings object with a MagicMock whose int() = 1.
        from backend.core.config import settings

        with patch("backend.routes.auth.redis_get", AsyncMock(return_value="1")):
            from backend.routes import auth as auth_mod

            with pytest.raises(HTTPException) as exc:
                import asyncio

                asyncio.run(auth_mod._check_otp_lockout("+15555550100"))

        assert exc.value.status_code == 429
        # Retry-After = OTP_LOCKOUT_DURATION_SECONDS — that's the
        # contract _record_otp_failure sets the lockout key to expire
        # at, so by then Redis will have evicted it and the next
        # send-otp will succeed.
        assert exc.value.headers["Retry-After"] == str(int(settings.OTP_LOCKOUT_DURATION_SECONDS))
        # IETF headers must mirror the slowapi 429 shape so the mobile
        # parser doesn't need to special-case OTP.
        assert exc.value.headers["RateLimit-Limit"] == str(int(settings.OTP_MAX_FAILURES))
        assert exc.value.headers["RateLimit-Remaining"] == "0"
        assert exc.value.headers["RateLimit-Reset"] == str(int(settings.OTP_LOCKOUT_DURATION_SECONDS))
