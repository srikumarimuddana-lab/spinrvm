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


def test_prefix_collision_is_bounded_by_route_level_auth():
    """A ride_id of literal "track" string-matches the exempt prefix.

    FastAPI would route e.g. GET /api/v1/rides/track/share to
    get_share_trip_link(ride_id="track"), which skips App Check by prefix. That
    is acceptable and deliberate: GET /track/{share_token} is the ONLY
    unauthenticated route under /api/v1/rides/ — every sibling still declares
    Depends(get_current_user), so the caller needs a valid rider JWT and then
    gets a 404 for the nonexistent ride. App Check is an attestation layer, not
    an authorisation one; dropping it here grants no access.

    This test pins the assumption: if a NEW unauthenticated route is ever added
    under /api/v1/rides/, revisit the exemption's prefix form.
    """
    sharing = (__import__("pathlib").Path(__file__).resolve().parents[1] / "routes" / "rides" / "sharing.py").read_text(
        encoding="utf-8"
    )

    # track_shared_ride is the only handler in the rides package declared
    # without an auth dependency; it takes share_token, not current_user.
    assert "async def track_shared_ride(share_token: str):" in sharing
    assert "current_user" not in sharing.split("async def track_shared_ride")[1].split("@router")[0]
