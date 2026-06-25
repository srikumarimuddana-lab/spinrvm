"""Regression tests for C1: production App Check / CSRF must never block inbound
server-to-server webhooks (Stripe, AWS SES/SNS).

Background
----------
FirebaseAppCheckMiddleware enforces an ``X-Firebase-AppCheck`` header on every
``/api/*`` path when ``ENV=production``. Stripe and AWS SES/SNS are external
systems that cannot attach that header, so unless the webhook mount point is on
``_APP_CHECK_EXEMPT_PREFIXES`` the request 401s "App Check token required"
*before* the handler runs — silently breaking payment confirmation, refunds,
disputes, payout reconciliation, subscription renewals, and email-bounce
handling the moment production enforcement turns on.

These tests resolve the *live* mounted webhook paths from the router objects
(not hardcoded strings) so the allowlist can never silently drift from the
mount point again.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _webhook_router():
    try:
        from routes.webhooks import api_router  # type: ignore
    except ImportError:  # pragma: no cover - import-path shim
        from backend.routes.webhooks import api_router  # type: ignore
    return api_router


def _live_webhook_paths():
    """Full, app-mounted paths of every webhook route, resolved by matching the
    webhook router's endpoint objects against the real app route table."""
    from backend.server import app

    webhook_endpoints = {
        getattr(r, "endpoint", None) for r in _webhook_router().routes if getattr(r, "endpoint", None) is not None
    }
    return [r.path for r in app.routes if getattr(r, "endpoint", None) in webhook_endpoints]


def test_live_webhook_paths_resolve_to_stripe_and_ses():
    """Sanity: the resolver actually finds the mounted webhook endpoints, and
    they live under /api/v1/webhooks/ as the exempt prefix expects."""
    paths = _live_webhook_paths()
    assert paths, "no webhook routes resolved from the live app route table"
    assert any(p.endswith("/webhooks/stripe") for p in paths), paths
    assert any(p.endswith("/webhooks/ses") for p in paths), paths
    assert all("/api/v1/webhooks/" in p for p in paths), paths


def test_webhook_paths_are_appcheck_exempt():
    """Every live webhook path must match an App Check exempt prefix — else
    production enforcement 401s the external caller before the handler runs."""
    from core.middleware import _APP_CHECK_EXEMPT_PREFIXES

    for path in _live_webhook_paths():
        assert any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES), (
            f"{path} is not App Check exempt — production App Check will 401 the webhook"
        )


def test_webhook_paths_are_csrf_exempt():
    """Webhooks carry no CSRF cookie; they must be CSRF-exempt too."""
    from core.middleware import _CSRF_EXEMPT_EXACT, _CSRF_EXEMPT_PREFIXES

    for path in _live_webhook_paths():
        exempt = path in _CSRF_EXEMPT_EXACT or any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)
        assert exempt, f"{path} is not CSRF exempt"


def test_stale_stripe_webhook_path_is_gone():
    """The never-matched legacy path must not linger in either exempt list."""
    from core.middleware import _APP_CHECK_EXEMPT_PREFIXES, _CSRF_EXEMPT_EXACT

    assert "/api/v1/stripe/webhook" not in _CSRF_EXEMPT_EXACT
    assert "/api/v1/stripe/webhook" not in _APP_CHECK_EXEMPT_PREFIXES


@pytest.mark.anyio
async def test_appcheck_lets_stripe_webhook_through_under_enforcement():
    """With enforcement ON and no App Check header, the Stripe webhook path
    passes through to the handler (call_next) instead of returning 401."""
    from core.middleware import FirebaseAppCheckMiddleware

    middleware = FirebaseAppCheckMiddleware(app=MagicMock(), enforcement_enabled=True)

    request = MagicMock()
    request.url.path = "/api/v1/webhooks/stripe"
    request.headers = {}  # no X-Firebase-AppCheck — Stripe can't send one
    request.state.request_id = "req-stripe-1"

    called = {"next": False}

    async def call_next(_):
        called["next"] = True
        return MagicMock(status_code=200)

    response = await middleware.dispatch(request, call_next)

    assert called["next"] is True, "webhook was blocked before reaching the handler"
    assert response.status_code == 200


@pytest.mark.anyio
async def test_appcheck_still_enforces_on_non_webhook_api_path():
    """Guard against an over-broad exemption: a normal /api path with no token
    still 401s under enforcement."""
    from core.middleware import FirebaseAppCheckMiddleware

    middleware = FirebaseAppCheckMiddleware(app=MagicMock(), enforcement_enabled=True)

    request = MagicMock()
    request.url.path = "/api/v1/rides"
    request.headers = {}
    request.state.request_id = "req-rides-1"

    async def call_next(_):  # pragma: no cover - must NOT be reached
        return MagicMock(status_code=200)

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 401
