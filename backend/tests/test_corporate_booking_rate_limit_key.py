"""Corporate + admin portal review, gap #41: company_booking_limit was
keyed purely by client IP (utils.rate_limiter's default_limiter.key_func),
so an attacker who already holds a valid company-member session could
bypass the 30/hour guest-booking cap (2-3 customer SMS per booking) by
rotating source IPs. Fixed by keying on company_id instead, since every
route this limiter guards is scoped under /company/{company_id}/**.
"""

from __future__ import annotations

import pytest
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from utils.rate_limiter import get_company_booking_key


def _fake_request(*, company_id: str | None = None, headers: dict | None = None) -> Request:
    path_params = {"company_id": company_id} if company_id else {}
    raw_headers = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/company/{company_id}/bookings" if company_id else "/company//bookings",
        "path_params": path_params,
        "headers": raw_headers,
        "client": ("198.51.100.1", 12345),
        "query_string": b"",
        "app": None,
    }
    return Request(scope)


def test_key_scopes_by_company_id_not_ip():
    req = _fake_request(company_id="company_abc")
    assert get_company_booking_key(req) == "company_booking:company_abc"


def test_key_is_stable_across_different_source_ips():
    """The whole point of the fix: two requests for the SAME company from
    DIFFERENT source IPs must land in the SAME rate-limit bucket."""
    req_a = _fake_request(company_id="company_abc", headers={"cf-connecting-ip": "203.0.113.5"})
    req_b = _fake_request(company_id="company_abc", headers={"cf-connecting-ip": "203.0.113.99"})
    assert get_company_booking_key(req_a) == get_company_booking_key(req_b)


def test_key_differs_across_companies():
    """Two different companies must not share a rate-limit budget."""
    req_a = _fake_request(company_id="company_abc")
    req_b = _fake_request(company_id="company_xyz")
    assert get_company_booking_key(req_a) != get_company_booking_key(req_b)


def test_key_falls_back_to_ip_when_company_id_missing():
    """Defensive fallback if this limiter is ever reused on a route
    without a company_id path param — must not silently return an empty
    or falsy key (which AsyncLimiter._check treats as "skip the check")."""
    req = _fake_request(company_id=None, headers={"cf-connecting-ip": "203.0.113.5"})
    key = get_company_booking_key(req)
    assert key == "ip:203.0.113.5"


@pytest.mark.asyncio
async def test_company_booking_limit_blocks_ip_rotation_within_one_company():
    """End-to-end proof of the fix using the real AsyncLimiter/storage
    machinery (not just the key function in isolation): two requests for
    the SAME company from DIFFERENT source IPs must share one bucket, and
    a DIFFERENT company must get its own, independent bucket. Uses a
    throwaway limiter/storage pair (not the shared default_limiter, which
    tests globally disable via conftest's reset_rate_limiters) built the
    same way company_booking_limit itself is."""
    from limits.aio.storage import MemoryStorage

    from utils.async_limiter import AsyncLimiter

    limiter = AsyncLimiter(key_func=get_company_booking_key, storage=MemoryStorage())
    calls = []

    @limiter.limit("1/hour", key_func=get_company_booking_key)
    async def _fake_booking_endpoint(request: Request):
        calls.append(request)
        return "ok"

    req_company_a_ip_1 = _fake_request(company_id="company_abc", headers={"cf-connecting-ip": "203.0.113.1"})
    req_company_a_ip_2 = _fake_request(company_id="company_abc", headers={"cf-connecting-ip": "203.0.113.2"})
    req_company_b = _fake_request(company_id="company_xyz", headers={"cf-connecting-ip": "203.0.113.3"})

    await _fake_booking_endpoint(req_company_a_ip_1)
    assert len(calls) == 1

    with pytest.raises(RateLimitExceeded):
        await _fake_booking_endpoint(req_company_a_ip_2)
    assert len(calls) == 1  # the second, different-IP call for the SAME company was blocked

    await _fake_booking_endpoint(req_company_b)
    assert len(calls) == 2  # a DIFFERENT company gets its own, unblocked bucket
