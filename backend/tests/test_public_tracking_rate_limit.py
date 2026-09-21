"""GET /api/v1/rides/track/{share_token} must carry a rate limit.

Follow-up to the App Check exemption (PR #5658). That change made this endpoint
reachable from any browser; it is the only App-Check-exempt, token-authorised
public endpoint in the codebase, so nothing at the perimeter throttles it.

An undecorated route here is genuinely unlimited, not "covered by a default":
init_middleware sets app.state.limiter but never adds SlowAPIMiddleware, so
default_limits are consulted only through an explicit .limit() decorator.

Brute-forcing the token is not the concern — secrets.token_urlsafe(32) is 256
bits. Unauthenticated unlimited polling is: every call does at least one
get_rows("rides", {"shared_trip_token": ...}) lookup.
"""

from __future__ import annotations

import inspect

import pytest


def test_track_shared_ride_is_rate_limited():
    """An undecorated handler has no limit at all — catch a decorator removal."""
    from backend.routes.rides import sharing

    fn = sharing.track_shared_ride
    assert hasattr(fn, "__wrapped__"), (
        "track_shared_ride is not wrapped by a limiter decorator. Without one it "
        "is completely unlimited: no SlowAPIMiddleware is installed, so "
        "default_limits never apply to an undecorated route."
    )


def test_track_shared_ride_accepts_a_request_parameter():
    """AsyncLimiter.limit() needs it — it raises TypeError at decoration time."""
    from backend.routes.rides import sharing

    params = inspect.signature(inspect.unwrap(sharing.track_shared_ride)).parameters
    assert "request" in params
    # share_token stays first so the path parameter still binds correctly.
    assert list(params)[0] == "share_token"


def test_share_track_limit_is_a_real_limiter_not_a_noop():
    """Guard against share_track_limit degrading into a pass-through decorator.

    AsyncLimiter refuses to decorate a handler with no request/websocket
    parameter, so this raising is proof the object carries real limiter
    behaviour rather than silently wrapping nothing.
    """
    from backend.utils.rate_limiter import share_track_limit

    async def handler_without_request(share_token: str):  # pragma: no cover
        return None

    with pytest.raises(TypeError):
        share_track_limit(handler_without_request)


def test_share_track_limit_is_ip_keyed():
    """No user exists on this endpoint, so IP is the only available identity."""
    from backend.utils.rate_limiter import default_limiter, get_real_client_ip

    assert default_limiter._key_func is get_real_client_ip
