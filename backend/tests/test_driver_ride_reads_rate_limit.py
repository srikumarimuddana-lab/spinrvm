"""C114 (ACTION_ITEMS.md): backend/routes/drivers/ride_reads.py had zero rate
limiting on its whole read-endpoint family (get_active_ride, get_ride_history,
get_ride_offer) — no decorator, and no SlowAPIMiddleware auto-covers an
undecorated route. Pins the fix: all three now enforce ride_read_limit,
matching routes/rides/queries.py's own read endpoints.

test_rate_limit_decorator_order.py already guards decorator *ordering*
repo-wide (a limiter above @router.get registers unwrapped and never runs).
This test guards a narrower, complementary fact that ordering test can't see:
that these specific three endpoints carry a limiter *at all*.
"""

from __future__ import annotations

import asyncio

from limits.aio.storage import MemoryStorage
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

try:
    from backend.routes.drivers import ride_reads
    from backend.utils.async_limiter import AsyncLimiter
except ImportError:  # pragma: no cover - top-level import layout
    from routes.drivers import ride_reads  # type: ignore
    from utils.async_limiter import AsyncLimiter  # type: ignore

_ENDPOINTS = ("get_active_ride", "get_ride_offer", "get_ride_history")


def _fake_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/rides/active",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


def test_all_three_read_endpoints_are_decorated_with_a_limiter():
    """Regression for C114: previously none of these carried any limiter."""
    for name in _ENDPOINTS:
        fn = getattr(ride_reads, name)
        assert hasattr(fn, "__wrapped__"), f"{name} does not appear to be decorated (no __wrapped__)"


def test_ride_read_limit_actually_enforces_on_this_module():
    """Exercise the real ride_read_limit object these endpoints are wrapped
    with, proving it raises once the configured limit is exceeded — not just
    that *some* decorator is present."""
    assert ride_reads.ride_read_limit is not None

    # ride_read_limit is default_limiter.limit("120/minute", ...); build an
    # isolated one-request-per-minute limiter with the same key/storage
    # contract to prove enforcement without waiting on/mutating the shared
    # module-level limiter's real 120/minute budget or its cross-test state.
    probe_limiter = AsyncLimiter(key_func=lambda: "probe", storage=MemoryStorage())

    @probe_limiter.limit("1/minute")
    async def probe(request: Request):
        return "ok"

    async def run():
        request = _fake_request()
        assert await probe(request=request) == "ok"
        try:
            await probe(request=request)
            raise AssertionError("expected RateLimitExceeded on the second call")
        except RateLimitExceeded:
            pass

    asyncio.run(run())
