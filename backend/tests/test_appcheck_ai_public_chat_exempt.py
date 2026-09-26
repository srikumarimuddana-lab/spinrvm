"""The public website assistant must be reachable with App Check enforced.

POST /api/v1/ai/public-chat is called by the spinr.ca chat widget — a browser,
which can never send X-Firebase-AppCheck. With the path not exempt, production
answered every visitor message with 401 "App Check token required" (access log,
2026-09-25). The endpoint is anonymous by design; its controls are the
ai_public_chat_enabled kill switch, the per-IP rate limit and the read-only
"web" tool audience, not App Check.

The signed-in assistant routes (/ai/chat, /ai/config, /ai/conversations) are
mobile-app surfaces and must stay App-Check-enforced.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.core.middleware import (
    _APP_CHECK_EXEMPT_PREFIXES,
    _CSRF_EXEMPT_EXACT,
    CSRFMiddleware,
    FirebaseAppCheckMiddleware,
)

PUBLIC_CHAT = "/api/v1/ai/public-chat"
ENFORCED = ("/api/v1/ai/chat", "/api/v1/ai/config", "/api/v1/ai/conversations")


@pytest.fixture
def enforced_client():
    async def handler(request):
        return PlainTextResponse("handler reached")

    app = Starlette(routes=[Route(path, handler, methods=["GET", "POST"]) for path in (PUBLIC_CHAT, *ENFORCED)])
    app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=True)
    # Match the live relative ordering: init_middleware registers CSRF after
    # App Check, making CSRF the outer layer that sees browser requests first.
    app.add_middleware(CSRFMiddleware)
    return TestClient(app)


def test_public_chat_reaches_its_handler_without_app_check(enforced_client):
    resp = enforced_client.post(
        PUBLIC_CHAT,
        json={"message": "hi"},
        headers={"Origin": "https://spinr.ca"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.text == "handler reached"


@pytest.mark.parametrize("path", ENFORCED)
def test_signed_in_ai_routes_stay_enforced(enforced_client, path):
    resp = enforced_client.post(path, json={})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "App Check token required"}


def test_exemption_is_the_exact_route_not_the_ai_prefix():
    assert "/api/v1/ai/" not in _APP_CHECK_EXEMPT_PREFIXES
    assert "/api/v1/ai" not in _APP_CHECK_EXEMPT_PREFIXES
    assert PUBLIC_CHAT in _APP_CHECK_EXEMPT_PREFIXES
    assert PUBLIC_CHAT in _CSRF_EXEMPT_EXACT


def test_exempt_path_is_the_real_public_chat_route():
    # Pins the exempted string to the path the real app serves, so a router
    # prefix change cannot silently re-break the website widget.
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
    assert PUBLIC_CHAT in served
    # No other served route may fall inside the startswith exemption.
    assert [p for p in served if p.startswith(PUBLIC_CHAT)] == [PUBLIC_CHAT]
