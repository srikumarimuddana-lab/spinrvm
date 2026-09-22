"""Signature-authenticated provider webhooks must be reachable with App Check
enforced.

Stripe and Twilio call /api/v1/webhooks/* from their own servers and can never
send X-Firebase-AppCheck. With enforcement re-enabled and those paths not
exempt, production answered every Stripe delivery with 401 "App Check token
required" — no webhook was received from 2026-09-17 03:15 UTC until the fix.
Each exempt handler authenticates the provider by signature instead.

SES stays enforced for now: its SNS topic allowlist fails open while
settings.aws_ses_sns_topic_arn is blank (as in production), so exempting it
would let any SNS topic post forged bounces. Flip that assertion only together
with configuring the ARN.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES, FirebaseAppCheckMiddleware

EXEMPT_WEBHOOKS = ("/api/v1/webhooks/stripe", "/api/v1/webhooks/twilio-inbound")
ENFORCED = ("/api/v1/webhooks/ses", "/api/v1/rides")


@pytest.fixture
def enforced_client():
    async def handler(request):
        return PlainTextResponse("handler reached")

    app = Starlette(routes=[Route(path, handler, methods=["POST"]) for path in (*EXEMPT_WEBHOOKS, *ENFORCED)])
    app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=True)
    return TestClient(app)


@pytest.mark.parametrize("path", EXEMPT_WEBHOOKS)
def test_signed_webhooks_reach_their_handler_without_app_check(enforced_client, path):
    resp = enforced_client.post(path, content=b"{}")

    assert resp.status_code == 200, resp.text
    assert resp.text == "handler reached"


@pytest.mark.parametrize("path", ENFORCED)
def test_ses_and_app_endpoints_stay_enforced(enforced_client, path):
    resp = enforced_client.post(path, content=b"{}")

    assert resp.status_code == 401
    assert resp.json() == {"detail": "App Check token required"}


def test_exemptions_are_exact_webhook_paths_not_the_whole_prefix():
    assert "/api/v1/webhooks/" not in _APP_CHECK_EXEMPT_PREFIXES
    assert set(EXEMPT_WEBHOOKS) <= set(_APP_CHECK_EXEMPT_PREFIXES)


def test_exempt_paths_are_the_real_webhook_routes():
    # The stub app above can't notice the webhooks router moving; this pins the
    # exempted strings to the paths the real app actually serves.
    #
    # `app.routes` no longer holds flat, path-bearing route objects directly —
    # FastAPI 0.136.1 -> 0.141.1's include_router() wraps a nested router in a
    # lazy `_IncludedRouter` (see test_documents.py::test_websocket_route_is_
    # registered and test_appcheck_public_tracking_exempt.py's own
    # `_effective_route_contexts()` for the same fix elsewhere) — walk its
    # `effective_route_contexts()` to get the real, fully-prefixed paths.
    from backend.server import app

    def _served_paths(routes):
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                for ctx in r.effective_route_contexts():
                    if ctx.path_format:
                        yield ctx.path_format
            else:
                path = getattr(r, "path", None)
                if path:
                    yield path

    served = set(_served_paths(app.routes))
    assert set(EXEMPT_WEBHOOKS) <= served
    assert "/api/v1/webhooks/ses" in served  # still served, just App-Check-enforced
