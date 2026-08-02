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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _app_with_auth_router() -> FastAPI:
    """Build a stripped FastAPI app that mounts only the auth router and
    wires the slowapi handler — bypasses server.py's full middleware
    stack (which pulls in multipart parsers we don't need here)."""
    # conftest puts both backend/ and the project root on sys.path, so
    # both `from utils...` and `from backend.utils...` resolve. Use the
    # bare path to match the routes' own imports.
    from dependencies import get_current_user
    from routes.auth import api_router
    from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(api_router)

    async def fake_user():
        return {"id": "rider-x", "phone": "+15551234567"}

    app.dependency_overrides[get_current_user] = fake_user
    return app


# ─────────────────────────────────────────────────────────────────────────────
# slowapi 429 — /auth/logout (3/minute) is the canonical example
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def enabled_limiter():
    """Re-enable the default limiter for the slowapi-window contract tests.

    conftest's autouse ``reset_rate_limiters`` fixture sets
    ``default_limiter.enabled = False`` before every test so unrelated tests
    can't trip 429s — but these two tests exist precisely to exercise the
    slowapi window path, so they must opt back in. Storage was already reset
    by the autouse fixture, so the window starts clean. Both dual-import
    module identities are toggled to mirror conftest's own loop.
    """
    import importlib

    limiters = []
    for rl_mod_path in ("backend.utils.rate_limiter", "utils.rate_limiter"):
        try:
            rl_mod = importlib.import_module(rl_mod_path)
        except (ImportError, ModuleNotFoundError):
            continue
        limiter = getattr(rl_mod, "default_limiter", None)
        if limiter is not None:
            limiter.enabled = True
            limiters.append(limiter)
    yield
    for limiter in limiters:
        limiter.enabled = False


class TestSlowapi429ResponseShape:
    """Pins the response shape for slowapi-tripped 429s. /auth/logout is
    rate-limited to 3/minute; the 4th call in the same window must
    return the documented headers + body."""

    def test_429_emits_retry_after_and_ratelimit_headers(self, enabled_limiter):
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

    def test_429_body_carries_retry_after_and_limit_fields(self, enabled_limiter):
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

    @pytest.mark.asyncio
    async def test_429_emits_rate_limit_violation_metric(self):
        """Corporate + admin portal review, SOC gap #46: every 429 through
        this handler must increment spinr_rate_limit_violation_total,
        labeled by path, so violations are visible on the infra
        monitoring page without grepping logs."""
        from unittest.mock import MagicMock, patch

        from fastapi import Request
        from limits import parse as parse_limit

        from utils.rate_limiter import rate_limit_exceeded_handler

        rl_item = parse_limit("5 per 1 minute")
        fake_exc = RateLimitExceeded(MagicMock(limit=rl_item))

        request = MagicMock(spec=Request)
        request.url.path = "/api/v1/rides"
        request.method = "POST"
        request.client = MagicMock(host="127.0.0.1")
        request.headers = {}

        with patch("utils.rate_limiter._metric_inc") as mock_inc:
            response = await rate_limit_exceeded_handler(request, fake_exc)

        assert response.status_code == 429
        mock_inc.assert_called_once_with("spinr_rate_limit_violation_total", {"path": "/api/v1/rides"})


# ─────────────────────────────────────────────────────────────────────────────
# OTP lockout 429 — separate handler, must mirror the same shape
# ─────────────────────────────────────────────────────────────────────────────


class TestOtpLockout429ResponseShape:
    """Pins the OTP lockout 429 contract. _check_otp_lockout raises
    HTTPException(429) directly (it doesn't go through slowapi), so its
    headers are independent of the rate_limit_exceeded_handler. Both
    paths must emit the same header set so the client parser is
    single-shape."""

    @pytest.mark.anyio
    async def test_otp_lockout_emits_retry_after_and_ratelimit_headers(self):
        # Force ENV != production so dev OTP bypass is irrelevant; the
        # lockout check runs ahead of OTP validation either way.
        os.environ.setdefault("ENV", "test")

        from unittest.mock import AsyncMock, patch

        from fastapi import HTTPException

        from backend.core.config import settings

        # Import auth via package attribute (from … import) so auth_mod
        # matches the live module object. Then patch redis_get on that
        # exact object. Using patch("backend.routes.auth.redis_get") can
        # target a stale sys.modules entry after test_p1_auth_hardening
        # deletes and re-imports backend.routes.auth inside a patch.dict
        # context — the sys.modules key is restored but the package
        # attribute sys.modules["backend.routes"].auth may still point to
        # the re-imported module.
        from backend.routes import auth as auth_mod

        with patch.object(auth_mod, "redis_get", AsyncMock(return_value="1")):
            with pytest.raises(HTTPException) as exc:
                await auth_mod._check_otp_lockout("+15555550100")

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
