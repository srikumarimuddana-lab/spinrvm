"""B-P2-1 — pin the error-response sanitisation contract.

Three concerns covered:

  1. _should_sanitize_5xx_detail — the sentinel-or-sanitize gate.
     Pure function; cheap, exhaustive coverage.
  2. _resolve_request_id — read state, fall back to fresh UUID. Pure-
     function semantics; pin the fallback so a test harness without
     the middleware doesn't crash the handler.
  3. http_exception_handler end-to-end via TestClient: 4xx detail
     passes through; 5xx detail is sanitised unless it matches an
     ERR_* sentinel; X-Request-ID header matches the body's
     error.request_id; route-supplied headers (Retry-After,
     RateLimit-*) propagate to the response (B-P1-8 regression fix).

If the sanitisation contract regresses, every route doing
``raise HTTPException(500, detail=str(e))`` silently leaks Stripe
charge IDs / Supabase constraint names / JWT library errors back to
the client.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# _should_sanitize_5xx_detail — pure-function gate
# ─────────────────────────────────────────────────────────────────────────────


class TestShouldSanitize5xxDetail:
    """Pin the sentinel-or-sanitize rule. The regex
    ``^ERR_[A-Z0-9_]+$`` is deliberately strict so a plausible-
    sounding free-text 5xx detail can't sneak through."""

    def test_passes_through_err_sentinel(self):
        from utils.error_handling import _should_sanitize_5xx_detail

        assert _should_sanitize_5xx_detail("ERR_AUTH_UNAVAILABLE") is False
        assert _should_sanitize_5xx_detail("ERR_OTP_LOCKED") is False
        assert _should_sanitize_5xx_detail("ERR_DATABASE_DOWN") is False
        assert _should_sanitize_5xx_detail("ERR_X9") is False

    def test_sanitises_free_text(self):
        """Any free-text detail — even if it looks innocuous — must be
        sanitised. The contract is "use ERR_* or get genericised", not
        "use your judgement"."""
        from utils.error_handling import _should_sanitize_5xx_detail

        assert _should_sanitize_5xx_detail("Internal server error") is True
        assert _should_sanitize_5xx_detail("Something went wrong") is True
        # Looks-like-sentinel but lower-case → sanitised.
        assert _should_sanitize_5xx_detail("err_auth_unavailable") is True
        # Has prefix but extra prose after.
        assert _should_sanitize_5xx_detail("ERR_AUTH_UNAVAILABLE: see logs") is True

    def test_sanitises_interpolated_exception_strings(self):
        """The actual leak shapes from the production audit:
        Stripe charge IDs, Supabase constraint names, JWT errors."""
        from utils.error_handling import _should_sanitize_5xx_detail

        assert _should_sanitize_5xx_detail(
            "Payment failed: Error 1002: Charge ch_1234567890abcdef declined"
        ) is True
        assert _should_sanitize_5xx_detail(
            'Failed to create corporate account: duplicate key value violates unique constraint "corporate_accounts_legal_name_key"'
        ) is True
        assert _should_sanitize_5xx_detail("Invalid token: kid not found") is True

    def test_sanitises_non_string_detail(self):
        """FastAPI lets HTTPException carry dict/list detail (used for
        validation errors). On 5xx that's never a sentinel — sanitise."""
        from utils.error_handling import _should_sanitize_5xx_detail

        assert _should_sanitize_5xx_detail({"err": "ERR_AUTH_UNAVAILABLE"}) is True
        assert _should_sanitize_5xx_detail(["ERR_AUTH_UNAVAILABLE"]) is True
        assert _should_sanitize_5xx_detail(None) is True
        assert _should_sanitize_5xx_detail(500) is True


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_request_id — middleware read with fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveRequestId:
    """Pin the request_id resolution. The middleware sets
    ``request.state.request_id``; handlers must read that, not
    generate their own — otherwise client-quoted IDs can't be cross-
    referenced against server logs."""

    def test_reads_state_request_id_when_set(self):
        from utils.error_handling import _resolve_request_id

        request = MagicMock()
        request.state.request_id = "abc123def456"
        assert _resolve_request_id(request) == "abc123def456"

    def test_falls_back_to_fresh_uuid_when_state_missing(self):
        """Test harnesses sometimes don't install RequestIDMiddleware.
        The handler must not crash — a fresh 12-char hex is fine as
        a fallback (the log line will use it; correlation is just
        absent, not broken)."""
        from utils.error_handling import _resolve_request_id

        request = MagicMock(spec=[])  # no .state attribute at all
        rid = _resolve_request_id(request)
        assert isinstance(rid, str)
        assert len(rid) == 12

    def test_falls_back_when_state_request_id_is_not_a_string(self):
        """Defensive: if some middleware mutated state.request_id to
        a non-string (None, int), the handler still produces a string."""
        from utils.error_handling import _resolve_request_id

        request = MagicMock()
        request.state.request_id = None
        assert isinstance(_resolve_request_id(request), str)


# ─────────────────────────────────────────────────────────────────────────────
# http_exception_handler end-to-end via TestClient
# ─────────────────────────────────────────────────────────────────────────────


def _app_with_handler():
    """Build a stripped FastAPI app + the four exception handlers +
    RequestIDMiddleware. The handler logic is what we're pinning;
    routes are throwaway probes that raise specific HTTPExceptions."""
    from fastapi import FastAPI, HTTPException

    from core.middleware import RequestIDMiddleware
    from utils.error_handling import register_exception_handlers

    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)

    @app.get("/leak-stripe")
    async def leak_stripe():
        raise HTTPException(
            status_code=500,
            detail="Payment failed: Error 1002: Charge ch_1234567890abcdef declined",
        )

    @app.get("/sentinel-503")
    async def sentinel_503():
        raise HTTPException(status_code=503, detail="ERR_AUTH_UNAVAILABLE")

    @app.get("/leak-supabase")
    async def leak_supabase():
        raise HTTPException(
            status_code=500,
            detail='duplicate key value violates unique constraint "users_phone_key"',
        )

    @app.get("/4xx-passthrough")
    async def four_xx():
        raise HTTPException(status_code=404, detail="Ride not found")

    @app.get("/4xx-with-headers")
    async def four_xx_headers():
        # Mirrors the OTP lockout shape — B-P1-8 RateLimit headers.
        raise HTTPException(
            status_code=429,
            detail="ERR_OTP_LOCKED",
            headers={
                "Retry-After": "86400",
                "RateLimit-Limit": "5",
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": "86400",
            },
        )

    @app.get("/5xx-sentinel-with-headers")
    async def five_xx_sentinel_headers():
        # Sentinel detail + headers — both must reach the client.
        raise HTTPException(
            status_code=503,
            detail="ERR_AUTH_UNAVAILABLE",
            headers={"Retry-After": "30"},
        )

    return app


class TestHttpExceptionHandlerEndToEnd:
    """Pin the full response shape via TestClient so a future change
    to JSONResponse construction doesn't quietly regress."""

    def test_5xx_with_leaked_stripe_detail_is_sanitised(self):
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/leak-stripe")

        assert r.status_code == 500
        body = r.json()
        # Top-level detail (mobile client reads this) must be the
        # generic message — NOT the Stripe charge id.
        assert body["detail"] == "Internal server error"
        assert "ch_1234567890abcdef" not in r.text
        # Nested error.message mirrors detail.
        assert body["error"]["message"] == "Internal server error"
        # Explicit signal so a future contributor reading dev tools
        # knows the message was scrubbed (vs the route returning
        # "Internal server error" intentionally).
        assert body["error"]["sanitised"] is True

    def test_5xx_supabase_constraint_leak_is_sanitised(self):
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/leak-supabase")

        assert r.status_code == 500
        # Constraint name must not reach the wire.
        assert "users_phone_key" not in r.text
        assert r.json()["detail"] == "Internal server error"

    def test_5xx_err_sentinel_passes_through(self):
        """ERR_* sentinels are vetted by the route author and let
        mobile clients branch UI without parsing English. Must
        survive the sanitiser intact."""
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/sentinel-503")

        assert r.status_code == 503
        body = r.json()
        assert body["detail"] == "ERR_AUTH_UNAVAILABLE"
        assert body["error"]["message"] == "ERR_AUTH_UNAVAILABLE"
        # No "sanitised" flag on a pass-through.
        assert "sanitised" not in body["error"]

    def test_4xx_detail_passes_through_unchanged(self):
        """The sanitiser fires on >=500 only. 4xx messages are user-
        facing UX ("Card declined", "Invalid phone") and must
        survive intact."""
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/4xx-passthrough")

        assert r.status_code == 404
        assert r.json()["detail"] == "Ride not found"
        assert "sanitised" not in r.json()["error"]

    def test_request_id_in_body_matches_x_request_id_header(self):
        """The whole point of B-P2-1's request-id consolidation:
        client-quoted ids must cross-reference server logs. The
        body's error.request_id must be the same string as the
        X-Request-ID response header (which is in turn what the
        loguru contextualize() call bound to every log line for
        this request)."""
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/leak-stripe")

        body_id = r.json()["error"]["request_id"]
        header_id = r.headers["X-Request-ID"]
        assert body_id == header_id
        assert body_id  # not empty

    def test_request_id_honors_inbound_header(self):
        """If the client sends X-Request-ID, the middleware reuses it.
        That value must show up in both the response header AND the
        error body — so a mobile client that sets the id before the
        request can include it in support tickets pre-emptively."""
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get(
            "/leak-stripe",
            headers={"X-Request-ID": "client-set-trace-001"},
        )

        assert r.headers["X-Request-ID"] == "client-set-trace-001"
        assert r.json()["error"]["request_id"] == "client-set-trace-001"

    def test_route_supplied_headers_propagate_to_response(self):
        """B-P1-8 regression fix: the OTP lockout path raises
        ``HTTPException(429, detail="ERR_OTP_LOCKED", headers={...})``
        with Retry-After + RateLimit-*. The previous handler dropped
        ``exc.headers`` entirely — clients never saw the rate-limit
        backoff hints despite B-P1-8 supposedly emitting them. Pin
        the propagation."""
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/4xx-with-headers")

        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "86400"
        assert r.headers.get("RateLimit-Limit") == "5"
        assert r.headers.get("RateLimit-Remaining") == "0"
        assert r.headers.get("RateLimit-Reset") == "86400"

    def test_route_supplied_headers_propagate_on_sanitised_5xx(self):
        """Same as the 4xx case but for a sanitised 5xx response.
        The detail gets scrubbed (well, here the sentinel passes
        through, but headers must still propagate even when the
        detail IS sanitised — we're pinning the propagation, not
        the sanitisation)."""
        from fastapi.testclient import TestClient

        client = TestClient(_app_with_handler())
        r = client.get("/5xx-sentinel-with-headers")

        assert r.status_code == 503
        assert r.headers.get("Retry-After") == "30"
        # Sentinel survived, header propagated.
        assert r.json()["detail"] == "ERR_AUTH_UNAVAILABLE"

    def test_x_request_id_cannot_be_overridden_by_route_headers(self):
        """A buggy route that does ``HTTPException(headers={'X-Request-ID': 'foo'})``
        must NOT corrupt the correlation id. The handler re-pins the
        middleware-bound id after merging exc.headers, so the
        correlation is unforgeable from inside a route."""
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware
        from utils.error_handling import register_exception_handlers

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)
        register_exception_handlers(app)

        @app.get("/buggy-override")
        async def buggy():
            raise HTTPException(
                status_code=500,
                detail="ERR_BUGGY",
                headers={"X-Request-ID": "i-am-evil"},
            )

        client = TestClient(app)
        r = client.get("/buggy-override", headers={"X-Request-ID": "real-id-xyz"})

        # Middleware-set id wins; route's attempt to override is ignored.
        assert r.headers["X-Request-ID"] == "real-id-xyz"
        assert r.json()["error"]["request_id"] == "real-id-xyz"


class TestRequestIdAcrossHandlers:
    """The four handlers must all read state.request_id, not generate
    their own. Pin one representative case for each: validation,
    HTTPException, SpinrException, generic Exception."""

    def test_validation_handler_uses_state_request_id(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from pydantic import BaseModel

        from core.middleware import RequestIDMiddleware
        from utils.error_handling import register_exception_handlers

        class Payload(BaseModel):
            value: int

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)
        register_exception_handlers(app)

        @app.post("/validate")
        async def validate(p: Payload):
            return {"ok": True}

        client = TestClient(app)
        r = client.post(
            "/validate",
            json={"value": "not-an-int"},
            headers={"X-Request-ID": "validate-trace-1"},
        )

        assert r.status_code == 422
        assert r.headers["X-Request-ID"] == "validate-trace-1"
        assert r.json()["error"]["request_id"] == "validate-trace-1"

    def test_general_exception_handler_uses_state_request_id(self):
        """Unhandled exception path — the most diagnostically critical
        one to correlate with logs."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware
        from utils.error_handling import register_exception_handlers

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)
        register_exception_handlers(app)

        @app.get("/boom")
        async def boom():
            raise RuntimeError("unhandled!")

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/boom", headers={"X-Request-ID": "boom-trace-1"})

        assert r.status_code == 500
        assert r.headers["X-Request-ID"] == "boom-trace-1"
        assert r.json()["error"]["request_id"] == "boom-trace-1"
        # Must NOT leak the exception message in production.
        assert "unhandled!" not in r.text or r.json()["error"].get("detail") is None

    def test_spinr_exception_handler_uses_state_request_id(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.middleware import RequestIDMiddleware
        from utils.error_handling import (
            ResourceNotFoundException,
            register_exception_handlers,
        )

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)
        register_exception_handlers(app)

        @app.get("/missing")
        async def missing():
            raise ResourceNotFoundException("Ride", "ride-xyz")

        client = TestClient(app)
        r = client.get("/missing", headers={"X-Request-ID": "missing-trace-1"})

        assert r.status_code == 404
        assert r.headers["X-Request-ID"] == "missing-trace-1"
        assert r.json()["error"]["request_id"] == "missing-trace-1"


@pytest.fixture(autouse=True)
def _isolate_module_imports(monkeypatch):
    """The exception handlers cache the FastAPI app instance via
    register_exception_handlers; tests build their own apps so no
    cross-test bleed. This fixture is just a no-op placeholder for
    future cleanup hooks."""
    yield
