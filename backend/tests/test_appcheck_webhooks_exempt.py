"""Provider webhooks must be reachable with App Check enforced.

Stripe, SES-via-SNS and Twilio call /api/v1/webhooks/* from their own servers
and can never send X-Firebase-AppCheck. With enforcement re-enabled and the
prefix not exempt, production answered every Stripe delivery with 401 "App
Check token required" — no webhook was received from 2026-09-17 03:15 UTC
until the fix. Each handler authenticates the provider by signature instead.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES, FirebaseAppCheckMiddleware

WEBHOOK_PATHS = ("/api/v1/webhooks/stripe", "/api/v1/webhooks/ses", "/api/v1/webhooks/twilio-inbound")


@pytest.fixture
def enforced_client():
    async def handler(request):
        return PlainTextResponse("handler reached")

    app = Starlette(
        routes=[Route(path, handler, methods=["POST"]) for path in (*WEBHOOK_PATHS, "/api/v1/rides")],
    )
    app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=True)
    return TestClient(app)


@pytest.mark.parametrize("path", WEBHOOK_PATHS)
def test_webhooks_reach_their_handler_without_app_check(enforced_client, path):
    resp = enforced_client.post(path, content=b"{}")

    assert resp.status_code == 200, resp.text
    assert resp.text == "handler reached"


def test_app_endpoints_stay_enforced(enforced_client):
    resp = enforced_client.post("/api/v1/rides", content=b"{}")

    assert resp.status_code == 401
    assert resp.json() == {"detail": "App Check token required"}


def test_exemption_is_scoped_to_the_webhooks_prefix():
    assert "/api/v1/webhooks/" in _APP_CHECK_EXEMPT_PREFIXES
    # A sibling path merely starting with "webhooks" is not exempt.
    assert not any("/api/v1/webhooksx".startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES)
