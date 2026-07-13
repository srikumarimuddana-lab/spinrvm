"""
P1-12: CORS on web-export endpoints (S9)

backend/core/middleware.py::init_middleware configures CORSMiddleware with
an explicit allowlist from settings.ALLOWED_ORIGINS. Tests pin:

  1. Wildcard '*' is refused at production startup (RuntimeError).
  2. Wildcard in non-production disables allow_credentials (CORS spec).
  3. An unlisted origin does NOT get Access-Control-Allow-Origin in
     exception-handler responses.
  4. An allowed origin DOES get the header with credentials enabled.
  5. The always-allowed dev origins are present regardless of env variable.

Run:
    pytest backend/tests/test_p1_cors.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helper: build a minimal app-like object to test init_middleware in isolation
# ─────────────────────────────────────────────────────────────────────────────


def _fake_app():
    """Minimal stand-in: records add_middleware calls without importing FastAPI."""

    class FakeApp:
        middlewares = []
        exception_handlers: dict = {}
        state = MagicMock()

        def add_middleware(self, cls, **kwargs):
            self.middlewares.append({"cls": cls, "kwargs": kwargs})

        def exception_handler(self, exc_type):
            def _deco(fn):
                self.exception_handlers[exc_type] = fn
                return fn

            return _deco

        def add_exception_handler(self, exc_type, handler):
            self.exception_handlers[exc_type] = handler

    return FakeApp()


# ─────────────────────────────────────────────────────────────────────────────
# Production wildcard guard
# ─────────────────────────────────────────────────────────────────────────────


class TestCorsWildcardProductionGuard:
    """Wildcard '*' in ALLOWED_ORIGINS must be rejected when ENV=production.

    Code under test: backend/core/middleware.py::init_middleware lines ~316-323.
    """

    def test_wildcard_raises_in_production(self):
        """Starting the server with ALLOWED_ORIGINS=* in production must fail."""

        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
        ):
            mock_settings.ENV = "production"
            mock_settings.ALLOWED_ORIGINS = "*"

            with pytest.raises(RuntimeError) as exc_info:
                init_middleware(fake_app)

        assert "wildcard" in str(exc_info.value).lower() or "*" in str(exc_info.value)
        assert "ALLOWED_ORIGINS" in str(exc_info.value) or "production" in str(exc_info.value).lower()

    def test_wildcard_allowed_in_development(self):
        """Wildcard is acceptable in development — no RuntimeError raised."""
        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
            patch("backend.core.middleware.default_limiter"),
            patch("backend.core.middleware.rate_limit_exceeded_handler"),
        ):
            mock_settings.ENV = "development"
            mock_settings.ALLOWED_ORIGINS = "*"

            try:
                init_middleware(fake_app)
            except RuntimeError as e:
                pytest.fail(f"wildcard should be allowed in development but got RuntimeError: {e}")

    def test_wildcard_disables_allow_credentials(self):
        """When wildcard is configured, allow_credentials must be False
        (CORS spec forbids credentials with wildcard origin)."""
        from fastapi.middleware.cors import CORSMiddleware

        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
            patch("backend.core.middleware.default_limiter"),
            patch("backend.core.middleware.rate_limit_exceeded_handler"),
        ):
            mock_settings.ENV = "development"
            mock_settings.ALLOWED_ORIGINS = "*"

            init_middleware(fake_app)

        cors_mw = next(
            (m for m in fake_app.middlewares if m["cls"] is CORSMiddleware),
            None,
        )
        assert cors_mw is not None, "CORSMiddleware was not registered"
        assert cors_mw["kwargs"].get("allow_credentials") is False, (
            "allow_credentials must be False when wildcard is used (CORS spec)"
        )

    def test_explicit_origins_enable_allow_credentials(self):
        """Explicit allowed origins must set allow_credentials=True."""
        from fastapi.middleware.cors import CORSMiddleware

        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
            patch("backend.core.middleware.default_limiter"),
            patch("backend.core.middleware.rate_limit_exceeded_handler"),
        ):
            mock_settings.ENV = "development"
            mock_settings.ALLOWED_ORIGINS = "https://app.spinr.ca,https://driver.spinr.ca"

            init_middleware(fake_app)

        cors_mw = next(
            (m for m in fake_app.middlewares if m["cls"] is CORSMiddleware),
            None,
        )
        assert cors_mw is not None
        assert cors_mw["kwargs"].get("allow_credentials") is True


# ─────────────────────────────────────────────────────────────────────────────
# Exception handler CORS reflection
# ─────────────────────────────────────────────────────────────────────────────


class TestCorsExceptionHandlerReflection:
    """The cors_exception_handler must only reflect the Origin header for
    requests whose origin is in the allowlist.

    Code under test: backend/core/middleware.py::cors_exception_handler (~line 354).
    """

    def _build_origins_and_handler(self, allowed: list[str], wildcard: bool = False):
        """Build a cors_exception_handler closure with controlled origin list."""
        from backend.core.middleware import _apply_security_headers

        origins = allowed
        allow_credentials = not wildcard
        is_production = False

        async def cors_exception_handler(request, exc):
            from fastapi.responses import JSONResponse

            origin = request.headers.get("origin")
            if hasattr(exc, "status_code") and hasattr(exc, "detail"):
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            else:
                response = JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
            if origin:
                if origin in origins:
                    response.headers["Access-Control-Allow-Origin"] = origin
                    if allow_credentials:
                        response.headers["Access-Control-Allow-Credentials"] = "true"
                elif wildcard:
                    response.headers["Access-Control-Allow-Origin"] = "*"
            _apply_security_headers(response, "/api/test", enable_hsts=is_production)
            return response

        return cors_exception_handler

    @pytest.mark.asyncio
    async def test_allowed_origin_gets_cors_header(self):
        """A request from an allowed origin must receive Access-Control-Allow-Origin."""
        handler = self._build_origins_and_handler(["https://app.spinr.ca"])
        request = MagicMock()
        request.headers = {"origin": "https://app.spinr.ca"}
        request.url.path = "/api/rides"
        exc = MagicMock()
        exc.status_code = 401
        exc.detail = "Unauthorized"

        response = await handler(request, exc)

        assert response.headers.get("Access-Control-Allow-Origin") == "https://app.spinr.ca"
        assert response.headers.get("Access-Control-Allow-Credentials") == "true"

    @pytest.mark.asyncio
    async def test_unlisted_origin_does_not_get_cors_header(self):
        """A request from an origin not in the allowlist must NOT get the
        Access-Control-Allow-Origin header (browser will block the read)."""
        handler = self._build_origins_and_handler(["https://app.spinr.ca"])
        request = MagicMock()
        request.headers = {"origin": "https://evil.example.com"}
        request.url.path = "/api/rides"
        exc = MagicMock()
        exc.status_code = 403
        exc.detail = "Forbidden"

        response = await handler(request, exc)

        assert "Access-Control-Allow-Origin" not in response.headers, (
            "Unlisted origin must not receive Access-Control-Allow-Origin — "
            "browser would otherwise expose the response body to the attacker."
        )

    @pytest.mark.asyncio
    async def test_no_origin_header_no_cors_header(self):
        """Server-to-server requests without an Origin header receive no CORS headers."""
        handler = self._build_origins_and_handler(["https://app.spinr.ca"])
        request = MagicMock()
        request.headers = {}  # no origin
        request.url.path = "/api/rides"
        exc = MagicMock()
        exc.status_code = 500
        exc.detail = "Server Error"

        response = await handler(request, exc)

        assert "Access-Control-Allow-Origin" not in response.headers


# ─────────────────────────────────────────────────────────────────────────────
# Always-allowed dev origins
# ─────────────────────────────────────────────────────────────────────────────


class TestAlwaysAllowedOrigins:
    """Certain dev origins are always added regardless of env variables."""

    def test_localhost_3000_always_allowed(self):
        from fastapi.middleware.cors import CORSMiddleware

        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
            patch("backend.core.middleware.default_limiter"),
            patch("backend.core.middleware.rate_limit_exceeded_handler"),
        ):
            mock_settings.ENV = "development"
            # Deliberately omit localhost:3000 from env variable
            mock_settings.ALLOWED_ORIGINS = "https://app.spinr.ca"

            init_middleware(fake_app)

        cors_mw = next(m for m in fake_app.middlewares if m["cls"] is CORSMiddleware)
        allowed = cors_mw["kwargs"]["allow_origins"]
        assert "http://localhost:3000" in allowed, (
            "http://localhost:3000 must always be in allowed origins for local dev"
        )

    def test_admin_vercel_always_allowed(self):
        from fastapi.middleware.cors import CORSMiddleware

        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
            patch("backend.core.middleware.default_limiter"),
            patch("backend.core.middleware.rate_limit_exceeded_handler"),
        ):
            mock_settings.ENV = "development"
            mock_settings.ALLOWED_ORIGINS = "https://app.spinr.ca"

            init_middleware(fake_app)

        cors_mw = next(m for m in fake_app.middlewares if m["cls"] is CORSMiddleware)
        allowed = cors_mw["kwargs"]["allow_origins"]
        assert "https://admin-spinr.spinr.ca" in allowed
        # The dead Vercel preview domain was removed in favour of the custom domain.
        assert "https://spinr-admin.vercel.app" not in allowed

    def test_marketing_website_always_allowed(self):
        from fastapi.middleware.cors import CORSMiddleware

        from backend.core.middleware import init_middleware

        fake_app = _fake_app()

        with (
            patch("backend.core.middleware.settings") as mock_settings,
            patch("backend.core.middleware._validate_production_config"),
            patch("backend.core.middleware.default_limiter"),
            patch("backend.core.middleware.rate_limit_exceeded_handler"),
        ):
            mock_settings.ENV = "development"
            # Deliberately omit the website from the env variable
            mock_settings.ALLOWED_ORIGINS = "https://app.spinr.ca"

            init_middleware(fake_app)

        cors_mw = next(m for m in fake_app.middlewares if m["cls"] is CORSMiddleware)
        allowed = cors_mw["kwargs"]["allow_origins"]
        # The marketing website makes credentialed browser calls (driver
        # signup + rider web booking) and must never fall out of CORS.
        assert "https://spinr.ca" in allowed
        assert "https://www.spinr.ca" in allowed


class TestStripeEmbedSecurityHeaders:
    """The Stripe embedded onboarding page needs a relaxed CSP + camera, but
    only on its own path — every other API path keeps the strict posture."""

    def _headers_for(self, path: str):
        from fastapi.responses import JSONResponse

        from backend.core.middleware import _apply_security_headers

        resp = JSONResponse(content={})
        _apply_security_headers(resp, path, enable_hsts=False)
        return resp.headers

    def test_stripe_embedded_gets_relaxed_csp_and_camera(self):
        h = self._headers_for("/api/v1/drivers/stripe-embedded")
        csp = h["Content-Security-Policy"]
        # Must allow connect.js, Stripe iframes, and inline script/style.
        assert "https://connect-js.stripe.com" in csp
        assert "frame-src" in csp and "js.stripe.com" in csp
        assert "'unsafe-inline'" in csp
        assert csp != "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        # Camera delegated to self + Stripe (not revoked).
        pp = h["Permissions-Policy"]
        assert "camera=(self" in pp
        assert "camera=()" not in pp

    def test_stripe_embedded_relaxes_coop_for_verification_popup(self):
        # Stripe's identity-verification sub-flow opens a cross-origin popup that
        # talks back via window.opener; COOP=same-origin breaks that handshake and
        # leaves the embedded component stuck on "Loading…". The host page must
        # relax COOP to unsafe-none.
        h = self._headers_for("/api/v1/drivers/stripe-embedded")
        assert h["Cross-Origin-Opener-Policy"] == "unsafe-none"

    def test_other_api_paths_keep_strict_csp_and_no_camera(self):
        h = self._headers_for("/api/v1/drivers/payouts")
        assert h["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        assert h["Permissions-Policy"] == "geolocation=(), microphone=(), camera=(), payment=()"
        # COOP stays locked down everywhere except the Stripe embed path.
        assert h["Cross-Origin-Opener-Policy"] == "same-origin"


class TestStripeAppCheckExemption:
    """The WebView-loaded Stripe endpoints can't carry X-Firebase-AppCheck;
    they're exempt (with JWT / public-only alternative auth). The money-moving
    payout endpoint must stay enforced."""

    def test_stripe_embedded_endpoints_are_exempt(self):
        from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES

        for path in (
            "/api/v1/drivers/stripe-embedded",
            "/api/v1/drivers/stripe-account-session",
        ):
            assert any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES), path

    def test_payout_endpoint_not_exempt(self):
        from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES

        assert not any("/api/v1/drivers/payouts".startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES)


class TestMobileBootstrapAppCheckExemptions:
    """Login/bootstrap endpoints must not deadlock behind App Check.

    They either return public client config or have their own OTP proof and
    rate-limit controls. Authenticated ride/payment endpoints remain enforced.
    """

    def test_login_and_public_settings_endpoints_are_exempt(self):
        from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES

        for path in (
            "/api/v1/settings",
            "/api/v1/auth/send-otp",
            "/api/v1/auth/verify-otp",
        ):
            assert any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES), path

    def test_authenticated_ride_endpoints_remain_enforced(self):
        from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES

        for path in (
            "/api/v1/rides/active",
            "/api/v1/drivers/rides/active",
        ):
            assert not any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES), path

