"""The public trip-share tracking page must be reachable from a browser.

Regression test for the live-testing bug where a shared tracking link
(track.spinr.ca/{token}) rendered "Tracking unavailable / Load failed".

Contract:
  - GET /api/v1/rides/track/{share_token} is the browser-facing half of the
    trip-share safety feature. track.spinr.ca is a Next.js page, so it can
    never attach an X-Firebase-AppCheck header; under enforcement the request
    must still reach the handler.
  - Authorisation is the unguessable secrets.token_urlsafe(32) share token
    (24h expiry, enforced in routes/rides/sharing.py::track_shared_ride), NOT
    App Check — so exempting it removes no real protection.
  - Every OTHER route under /api/v1/rides/ keeps its get_current_user
    dependency and stays App-Check-enforced.

Why the failure was invisible: App Check short-circuits with a 401 from
*outside* CORSMiddleware (CORS is registered first in init_middleware, so it
sits innermost and never sees the short-circuit). The 401 therefore carries no
Access-Control-Allow-Origin, the browser blocks it, and fetch() rejects as a
generic network error ("Load failed" in Safari, "Failed to fetch" in Chrome)
rather than a readable 401 the page could report.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES, FirebaseAppCheckMiddleware

# A realistic share token: secrets.token_urlsafe(32) is ~43 URL-safe chars.
_SHARE_TOKEN = "i_akvx7KObaUkmUxaQKX_h8qHBjgQ2mE9rTzLpWn4dYs"


def _appcheck_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES)


async def _dispatch_without_token(path: str):
    """Run App Check in enforcement mode against a token-less GET."""
    middleware = FirebaseAppCheckMiddleware(app=MagicMock(), enforcement_enabled=True)

    request = MagicMock()
    request.url.path = path
    request.headers = {}
    request.state.request_id = "req-track-test"

    sentinel = MagicMock(status_code=200)

    async def call_next(_):
        return sentinel

    response = await middleware.dispatch(request, call_next)
    return response, sentinel


# ── The fix ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_public_tracking_reaches_handler_without_app_check_token():
    """The browser has no App Check token; the request must still get through."""
    response, sentinel = await _dispatch_without_token(f"/api/v1/rides/track/{_SHARE_TOKEN}")

    assert response is sentinel, (
        "App Check short-circuited the public tracking endpoint. In production "
        "this 401 is emitted outside CORSMiddleware, so the browser sees an "
        "opaque network error and the shared trip link renders "
        "'Tracking unavailable'."
    )
    assert response.status_code == 200


def test_tracking_prefix_is_registered():
    assert "/api/v1/rides/track/" in _APP_CHECK_EXEMPT_PREFIXES
    assert _appcheck_exempt(f"/api/v1/rides/track/{_SHARE_TOKEN}")


# ── The blast radius stays closed ─────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/rides",  # list/create
        "/api/v1/rides/active",
        "/api/v1/rides/history",
        "/api/v1/rides/ride_123",
        "/api/v1/rides/ride_123/share",  # mints the token — app-only
        "/api/v1/rides/ride_123/cancel",
        "/api/v1/rides/ride_123/receipt",
        "/api/v1/rides/tracking",  # near-miss: no trailing slash match
        "/api/v1/rides/track",  # the bare segment is not the route
    ],
)
def test_sibling_rides_routes_stay_enforced(path: str):
    """Only the /track/ sub-path is exempt — the prefix must not widen."""
    assert not _appcheck_exempt(path), f"{path} must stay App-Check-enforced"


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/v1/rides/ride_123/share", "/api/v1/rides/active"])
async def test_sibling_rides_routes_still_401_without_token(path: str):
    response, sentinel = await _dispatch_without_token(path)

    assert response is not sentinel
    assert response.status_code == 401


def _resolve_endpoint(method: str, path: str):
    """Return the endpoint function the real router resolves (method, path) to.

    Pure Starlette route matching — no DB, no auth, no middleware. This is the
    same first-full-match-wins walk that decides the live behaviour, so it pins
    the mechanism instead of assuming it.
    """
    from starlette.routing import Match

    from backend.server import app

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
    }
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "endpoint", None)
    return None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # sharing.py defines these BEFORE /track/{share_token} (lines 41 and
        # 210 vs 221), so the sibling handler wins and still demands a JWT.
        ("/api/v1/rides/track/share", "get_share_trip_link"),
        ("/api/v1/rides/track/shared-contacts", "get_shared_contacts"),
        # These live in routers registered AFTER `sharing` in
        # routes/rides/__init__.py, so /track/{share_token} matches first and
        # track_shared_ride handles them — looking up a share token literally
        # named "receipt"/"chat-status"/..., finding none, and 404ing.
        ("/api/v1/rides/track/receipt", "track_shared_ride"),
        ("/api/v1/rides/track/chat-status", "track_shared_ride"),
        ("/api/v1/rides/track/live-route", "track_shared_ride"),
        ("/api/v1/rides/track/navigation-steps", "track_shared_ride"),
    ],
)
def test_get_collision_resolves_as_documented(path: str, expected: str):
    """A ride_id of the literal string "track" collides with the exempt prefix.

    Which handler it reaches depends on router registration order, and it is NOT
    uniformly the sibling handler — the comment in core/middleware.py spells out
    both cases. Either resolution is safe (the sibling keeps
    Depends(get_current_user); track_shared_ride is public by design and 404s on
    a token that isn't one), but pinning it here means a future reordering of
    routes/rides/__init__.py or of sharing.py surfaces as a failing test rather
    than a silent change in which handler serves a colliding URL.
    """
    endpoint = _resolve_endpoint("GET", path)

    assert endpoint is not None, f"{path} resolved to no route at all"
    assert endpoint.__name__ == expected


def test_track_route_is_get_only_so_write_siblings_never_collide():
    """track_shared_ride is GET-only, so it must never intercept a write sibling.

    Asserted as "is not track_shared_ride" rather than by handler name: these
    routes carry wrapping decorators (@cancel_ride_limit, @ride_rating_limit,
    @idempotent_endpoint), and the invariant that matters is that a POST is
    never swallowed by the public read endpoint — not what the handler is called.
    """
    for path in (
        "/api/v1/rides/track/cancel",
        "/api/v1/rides/track/rate",
        "/api/v1/rides/track/start",
        "/api/v1/rides/track/complete",
    ):
        endpoint = _resolve_endpoint("POST", path)
        assert endpoint is not None, f"POST {path} resolved to no route at all"
        assert endpoint.__name__ != "track_shared_ride", (
            f"POST {path} resolved to track_shared_ride; it is GET-only and must never intercept a write sibling"
        )


def test_track_shared_ride_is_the_only_unauthenticated_rides_route():
    """If a NEW unauthenticated route appears under routes/rides/, the prefix
    form of this exemption needs revisiting — fail loudly when that happens.

    Keyed on ``__module__`` (preserved through functools.wraps) rather than
    ``inspect.getfile``, which reports the decorator's file for a wrapped
    handler and would silently skip every rate-limited route.
    """
    import inspect

    from backend.server import app

    unauthenticated: set[str] = set()

    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        module = getattr(endpoint, "__module__", "") or ""
        if "routes.rides" not in module:
            continue
        params = inspect.signature(inspect.unwrap(endpoint)).parameters
        if not any(p in params for p in ("current_user", "user")):
            unauthenticated.add(endpoint.__name__)

    assert unauthenticated == {"track_shared_ride"}, (
        f"unauthenticated routes under routes/rides/ changed: {sorted(unauthenticated)}. "
        "The /api/v1/rides/track/ App Check exemption is a str.startswith prefix — "
        "re-check that it still cannot expose a newly-public sibling."
    )
