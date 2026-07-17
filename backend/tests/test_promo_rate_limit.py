"""
Promo code endpoint rate-limit enforcement tests.

Limits (from backend/utils/rate_limiter.py):
  - /promo/validate  — promo_validate_limit  = 10 per minute
  - /promo/available — promo_available_limit = 20 per minute

Strategy
--------
Build a minimal FastAPI app with only the promo router and the real
slowapi limiter wired (same approach as test_rate_limit_response_shape.py).
This avoids the full server startup (Stripe, Firebase, Redis, etc.) while
exercising the actual decorator binding.

The conftest mocks slowapi OUT at module import time so existing routes
bound before the mock was applied keep their real limiters; but we import
the promo router AFTER mounting a fresh app so we can wire slowapi in the
standard way without fighting conftest's global replacement.

All DB calls are mocked so no real Supabase is needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

# Fake user returned by get_current_user dependency override.
_FAKE_USER = {
    "id": "promo_rl_user_1",
    "phone": "+15550009999",
    "role": "rider",
}

# Minimal valid promo row returned by DB mocks for /validate success tests.
_PROMO_ROW = {
    "id": "promo_abc",
    "code": "SPINR10",
    "is_active": True,
    "discount_type": "flat",
    "discount_value": 5.0,
    "max_uses": 0,
    "max_uses_per_user": 0,
    "uses": 0,
    "expiry_date": None,
    "assigned_user_ids": [],
    "first_ride_only": False,
    "new_user_days": 0,
    "inactive_days": 0,
    "min_total_rides": 0,
    "max_total_rides": 0,
    "total_budget": 0,
    "min_ride_fare": 0,
}


def _build_promo_app() -> FastAPI:
    """Minimal FastAPI app with only the promo router and real slowapi wired.

    Imports all modules FRESH (bare-path) so the real limiter decorators
    bound at import time are in effect — conftest's slowapi stub replacement
    happens only for modules it imported during collection, not for modules
    imported here by the local fixture.
    """
    from dependencies import get_current_user
    from routes.promotions import api_router as promo_router
    from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(promo_router)

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return app


# ── /promo/validate (10 per minute) ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enable_real_limiter():
    """The global conftest disables `default_limiter` (so most tests don't trip
    rate-limits incidentally). These tests need the real limiter ON to verify
    enforcement, so locally re-enable + reset its storage before each test."""
    from utils.rate_limiter import default_limiter

    default_limiter.enabled = True
    inner = getattr(default_limiter, "_limiter", None)
    storage = getattr(inner, "storage", None) if inner is not None else None
    if storage is not None and callable(getattr(storage, "reset", None)):
        result = storage.reset()
        if result is not None:
            asyncio.run(result)
    yield
    default_limiter.enabled = False


class TestPromoValidateRateLimit:
    """Rate-limit enforcement for POST /promo/validate (promo_validate_limit: 10/min)."""

    @staticmethod
    def _client_with_mocked_db() -> TestClient:
        app = _build_promo_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_promo_validate_under_limit(self):
        """10 requests within a minute should all succeed (200 or 4xx from
        business logic — anything except 429)."""
        client = self._client_with_mocked_db()

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[_PROMO_ROW])),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            for i in range(10):
                r = client.post("/promo/validate", json={"code": "SPINR10", "ride_fare": "10.00"})
                assert r.status_code != 429, (
                    f"Request {i + 1}/10 got 429 — rate limit tripped too early. Body: {r.text}"
                )

    def test_promo_validate_exceeds_limit(self):
        """The 11th request within the same minute window must return 429
        with the documented error body and Retry-After header."""
        client = self._client_with_mocked_db()

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[_PROMO_ROW])),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            # Burn through the 10-request budget.
            for _ in range(10):
                client.post("/promo/validate", json={"code": "SPINR10", "ride_fare": "10.00"})

            # 11th request must be rate-limited.
            r = client.post("/promo/validate", json={"code": "SPINR10", "ride_fare": "10.00"})

        assert r.status_code == 429, f"Expected 429 on 11th request, got {r.status_code}. Body: {r.text}"

        # Verify the response body matches the documented shape.
        body = r.json()
        assert body.get("error") == "rate_limit_exceeded", f"Unexpected body: {body}"
        assert "retry_after" in body, "Missing retry_after in 429 body"

        # Retry-After header is required by RFC 9110.
        assert "Retry-After" in r.headers, "Missing Retry-After header on 429"

    def test_promo_validate_exceeds_limit_retry_after_header(self):
        """Retry-After header value must equal the window size (60 s for /minute)."""
        client = self._client_with_mocked_db()

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[_PROMO_ROW])),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            for _ in range(10):
                client.post("/promo/validate", json={"code": "SPINR10", "ride_fare": "10.00"})
            r = client.post("/promo/validate", json={"code": "SPINR10", "ride_fare": "10.00"})

        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "60", (
            f"Expected Retry-After: 60 (window is /minute), got {r.headers.get('Retry-After')}"
        )
        # IETF draft-ietf-httpapi-ratelimit-headers
        assert r.headers.get("RateLimit-Limit") == "10"
        assert r.headers.get("RateLimit-Remaining") == "0"


# ── /promo/available (20 per minute) ─────────────────────────────────────────


class TestPromoAvailableRateLimit:
    """Rate-limit enforcement for GET /promo/available (promo_available_limit: 20/min)."""

    @staticmethod
    def _client_with_mocked_db() -> TestClient:
        app = _build_promo_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_promo_enumerate_under_limit(self):
        """20 requests within a minute should all succeed (200 — not 429)."""
        client = self._client_with_mocked_db()

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value=_FAKE_USER)),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            for i in range(20):
                r = client.get("/promo/available?ride_fare=10.00")
                assert r.status_code != 429, f"Request {i + 1}/20 got 429 — limit tripped too early. Body: {r.text}"

    def test_promo_enumerate_exceeds_limit(self):
        """The 21st request within the same minute window must return 429."""
        client = self._client_with_mocked_db()

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value=_FAKE_USER)),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            # Burn through the 20-request budget.
            for _ in range(20):
                client.get("/promo/available?ride_fare=10.00")

            # 21st request must be rate-limited.
            r = client.get("/promo/available?ride_fare=10.00")

        assert r.status_code == 429, f"Expected 429 on 21st request, got {r.status_code}. Body: {r.text}"

        body = r.json()
        assert body.get("error") == "rate_limit_exceeded", f"Unexpected body: {body}"
        assert "retry_after" in body
        assert "Retry-After" in r.headers

    def test_promo_enumerate_exceeds_limit_retry_after_header(self):
        """Retry-After must be 60 s (the /minute window) for the available endpoint."""
        client = self._client_with_mocked_db()

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value=_FAKE_USER)),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            for _ in range(20):
                client.get("/promo/available?ride_fare=10.00")
            r = client.get("/promo/available?ride_fare=10.00")

        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "60", f"Expected Retry-After: 60, got {r.headers.get('Retry-After')}"
        assert r.headers.get("RateLimit-Limit") == "20"
        assert r.headers.get("RateLimit-Remaining") == "0"


# ── Unit tests for rate_limit_exceeded_handler response shape ─────────────────


class TestRateLimitResponseShape:
    """Unit-level test for the rate-limit handler body shape so client
    parsers are guaranteed the documented fields regardless of endpoint."""

    @pytest.mark.anyio
    async def test_validate_limit_response_body_shape(self):
        """Build a synthetic RateLimitExceeded for the 10/minute constraint
        and assert the handler emits all documented fields."""
        from fastapi import Request
        from limits import parse as parse_limit

        from utils.rate_limiter import rate_limit_exceeded_handler

        rl_item = parse_limit("10 per 1 minute")
        fake_exc = RateLimitExceeded(MagicMock(limit=rl_item))

        request = MagicMock(spec=Request)
        request.url.path = "/promo/validate"
        request.method = "POST"
        request.client = MagicMock(host="127.0.0.1")
        request.headers = {}

        response = await rate_limit_exceeded_handler(request, fake_exc)

        assert response.status_code == 429
        import json as _json

        body = _json.loads(response.body)
        assert body["error"] == "rate_limit_exceeded"
        assert body["retry_after"] == 60
        assert body["limit"] == 10
        assert "documentation_url" in body

    @pytest.mark.anyio
    async def test_available_limit_response_body_shape(self):
        """Same for 20/minute — handler must parse the correct window and amount."""
        from fastapi import Request
        from limits import parse as parse_limit

        from utils.rate_limiter import rate_limit_exceeded_handler

        rl_item = parse_limit("20 per 1 minute")
        fake_exc = RateLimitExceeded(MagicMock(limit=rl_item))

        request = MagicMock(spec=Request)
        request.url.path = "/promo/available"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")
        request.headers = {}

        response = await rate_limit_exceeded_handler(request, fake_exc)

        assert response.status_code == 429
        import json as _json

        body = _json.loads(response.body)
        assert body["error"] == "rate_limit_exceeded"
        assert body["retry_after"] == 60
        assert body["limit"] == 20
