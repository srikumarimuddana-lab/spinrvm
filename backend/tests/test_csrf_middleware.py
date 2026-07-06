"""
Unit tests for the CSRF double-submit cookie middleware (backend/core/csrf.py
and the CSRFMiddleware class in backend/core/middleware.py).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub out heavy deps that middleware.py imports but the test env doesn't have.
_STUBS = ["slowapi", "slowapi.errors", "core.config", "utils.rate_limiter"]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# Save original settings so the autouse fixture can restore it after all
# module tests finish.  We must NOT mutate core.config.settings at module
# import time because pytest collects (imports) every test module before
# running any tests — a module-level mutation would contaminate other test
# modules (e.g. test_admin_logout_revocation) that run before this file's
# tests execute and therefore before the restore fixture fires.
_core_config_mod = sys.modules["core.config"]
_original_settings = getattr(_core_config_mod, "settings", None)


@pytest.fixture(scope="module", autouse=True)
def _restore_core_config_settings():
    # Apply the settings mock inside the fixture (not at import time) so it
    # is set just before this module's first test and restored after the last.
    _core_config_mod.settings = MagicMock(ENV="development")
    yield
    if _original_settings is not None:
        _core_config_mod.settings = _original_settings


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_app() -> tuple[FastAPI, TestClient]:
    """Return a minimal FastAPI app with CSRFMiddleware attached."""
    from core.middleware import CSRFMiddleware

    app = FastAPI()

    @app.post("/api/v1/test")
    async def test_post():
        return {"ok": True}

    @app.put("/api/v1/test")
    async def test_put():
        return {"ok": True}

    @app.get("/api/v1/test")
    async def test_get():
        return {"ok": True}

    @app.post("/api/v1/auth/send-otp")
    async def send_otp():
        return {"ok": True}

    @app.post("/api/admin/auth/login")
    async def admin_login():
        return {"ok": True}

    @app.post("/api/v1/stripe/webhook")
    async def stripe_webhook():
        return {"ok": True}

    @app.post("/api/v1/drivers/stripe-account-session")
    async def stripe_account_session():
        return {"ok": True}

    app.add_middleware(CSRFMiddleware)
    client = TestClient(app, raise_server_exceptions=True)
    return app, client


@pytest.fixture(scope="module")
def client():
    _, c = _make_app()
    return c


@pytest.fixture(scope="module")
def valid_csrf_token():
    from core.csrf import generate_csrf_token

    return generate_csrf_token()


# ── Safe methods ──────────────────────────────────────────────────────────────


class TestSafeMethods:
    def test_get_no_csrf_allowed(self, client: TestClient):
        res = client.get(
            "/api/v1/test",
            headers={"Origin": "https://example.com"},
        )
        assert res.status_code == 200

    def test_options_no_csrf_allowed(self, client: TestClient):
        res = client.options(
            "/api/v1/test",
            headers={"Origin": "https://example.com"},
        )
        # TestClient returns 405 for unregistered methods — still not 403
        assert res.status_code != 403


# ── No Origin header ─────────────────────────────────────────────────────────


class TestNoOriginHeader:
    """Requests without an Origin header (server-to-server, curl, mobile) skip CSRF."""

    def test_post_without_origin_allowed(self, client: TestClient):
        res = client.post("/api/v1/test")
        assert res.status_code == 200

    def test_put_without_origin_allowed(self, client: TestClient):
        res = client.put("/api/v1/test")
        assert res.status_code == 200


# ── Exempt paths ─────────────────────────────────────────────────────────────


class TestExemptPaths:
    def test_send_otp_exempt(self, client: TestClient):
        res = client.post(
            "/api/v1/auth/send-otp",
            headers={"Origin": "https://example.com"},
        )
        assert res.status_code == 200

    def test_admin_login_exempt(self, client: TestClient):
        res = client.post(
            "/api/admin/auth/login",
            headers={"Origin": "https://example.com"},
        )
        assert res.status_code == 200

    def test_stripe_account_session_exempt(self, client: TestClient):
        # The driver app's embedded-onboarding WebView posts here with an Origin
        # header but no csrf_token cookie; it authenticates via Bearer JWT, so it
        # must be exempt or the in-page fetch 403s (prod regression 2026-06-17).
        res = client.post(
            "/api/v1/drivers/stripe-account-session",
            headers={"Origin": "https://api-spinr.spinr.ca"},
        )
        assert res.status_code == 200

    def test_stripe_webhook_exempt(self, client: TestClient):
        res = client.post(
            "/api/v1/stripe/webhook",
            headers={"Origin": "https://example.com"},
        )
        assert res.status_code == 200


# ── Valid CSRF ────────────────────────────────────────────────────────────────


class TestValidCsrf:
    def test_post_with_matching_token_allowed(self, client: TestClient, valid_csrf_token: str):
        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": valid_csrf_token,
            },
            cookies={"csrf_token": valid_csrf_token},
        )
        assert res.status_code == 200

    def test_put_with_matching_token_allowed(self, client: TestClient, valid_csrf_token: str):
        res = client.put(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": valid_csrf_token,
            },
            cookies={"csrf_token": valid_csrf_token},
        )
        assert res.status_code == 200

    def test_company_namespace_cookie_allowed(self, client: TestClient, valid_csrf_token: str):
        """The company portal uses its own csrf_token_company cookie (staff and
        portal share the origin) — a header matching it must validate."""
        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": valid_csrf_token,
            },
            cookies={"csrf_token_company": valid_csrf_token},
        )
        assert res.status_code == 200

    def test_both_namespaces_present_header_matches_company(self, client: TestClient):
        """Both sessions active in one browser: the header must validate against
        whichever cookie it matches (here the company one), not just csrf_token —
        this is the collision the per-audience cookie fixes."""
        from core.csrf import generate_csrf_token

        admin_tok = generate_csrf_token()
        company_tok = generate_csrf_token()
        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": company_tok,
            },
            cookies={"csrf_token": admin_tok, "csrf_token_company": company_tok},
        )
        assert res.status_code == 200


# ── Invalid CSRF ──────────────────────────────────────────────────────────────


class TestInvalidCsrf:
    def test_post_missing_cookie_rejected(self, client: TestClient, valid_csrf_token: str):
        """Header present but no cookie → 403."""
        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": valid_csrf_token,
            },
        )
        assert res.status_code == 403

    def test_post_missing_header_rejected(self, client: TestClient, valid_csrf_token: str):
        """Cookie present but no header → 403."""
        res = client.post(
            "/api/v1/test",
            headers={"Origin": "https://example.com"},
            cookies={"csrf_token": valid_csrf_token},
        )
        assert res.status_code == 403

    def test_company_cookie_present_wrong_header_rejected(self, client: TestClient):
        """A header matching NEITHER present cookie is still rejected — the
        per-audience acceptance doesn't weaken the double-submit check."""
        from core.csrf import generate_csrf_token

        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": generate_csrf_token(),
            },
            cookies={"csrf_token_company": generate_csrf_token()},
        )
        assert res.status_code == 403

    def test_post_mismatched_tokens_rejected(self, client: TestClient):
        from core.csrf import generate_csrf_token

        token_a = generate_csrf_token()
        token_b = generate_csrf_token()
        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": token_a,
            },
            cookies={"csrf_token": token_b},
        )
        assert res.status_code == 403

    def test_post_empty_header_rejected(self, client: TestClient, valid_csrf_token: str):
        """Empty header value is treated as missing."""
        res = client.post(
            "/api/v1/test",
            headers={
                "Origin": "https://example.com",
                "X-CSRF-Token": "",
            },
            cookies={"csrf_token": valid_csrf_token},
        )
        assert res.status_code == 403

    def test_post_both_absent_rejected(self, client: TestClient):
        """No cookie and no header → 403 (not a server-to-server request since Origin present)."""
        res = client.post(
            "/api/v1/test",
            headers={"Origin": "https://example.com"},
        )
        assert res.status_code == 403


# ── Token generation ──────────────────────────────────────────────────────────


class TestCsrfTokenGeneration:
    def test_token_is_url_safe_string(self):
        from core.csrf import generate_csrf_token

        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) >= 32

    def test_tokens_are_unique(self):
        from core.csrf import generate_csrf_token

        tokens = {generate_csrf_token() for _ in range(20)}
        assert len(tokens) == 20

    def test_set_csrf_cookie(self):
        from fastapi import Response as FastAPIResponse

        from core.csrf import generate_csrf_token, set_csrf_cookie

        token = generate_csrf_token()
        response = FastAPIResponse()
        set_csrf_cookie(response, token, secure=False, max_age=3600)
        raw = response.headers.get("set-cookie", "")
        assert "csrf_token=" in raw
        # Must NOT be HttpOnly so browser JS can read it
        assert "httponly" not in raw.lower()

    def test_clear_csrf_cookie(self):
        from fastapi import Response as FastAPIResponse

        from core.csrf import clear_csrf_cookie

        response = FastAPIResponse()
        clear_csrf_cookie(response)
        raw = response.headers.get("set-cookie", "")
        assert "csrf_token=" in raw
        assert "max-age=0" in raw.lower()
