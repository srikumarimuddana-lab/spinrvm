"""Burst tolerance: rate limits must be per-user, not per carrier-NAT IP.

Mobile carriers put hundreds of subscribers behind one CGNAT egress IP. Under
IP keying every rider on a carrier shared ONE bucket, so a burst of legitimate
users 429'd itself — and an SOS could be refused because unrelated strangers on
the same egress IP tapped ride actions. `get_user_or_ip_key` keys on the
authenticated user instead, falling back to IP only for anonymous traffic.

See docs/change-log/2026-08-07-rate-limit-user-keying.md.
"""

from __future__ import annotations

import jwt
import pytest
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from utils.rate_limiter import get_user_or_ip_key

CARRIER_NAT_IP = "203.0.113.7"


def _token(user_id: str) -> str:
    """A bearer token. Signature is irrelevant — the key func decodes without
    verifying, and the real `get_current_user` dependency still gates the
    handler (see `_extract_unverified_user_id`'s docstring)."""
    return jwt.encode({"user_id": user_id}, "irrelevant-test-secret", algorithm="HS256")


def _fake_request(*, user_id: str | None = None, ip: str = CARRIER_NAT_IP, raw_auth: str | None = None) -> Request:
    headers = {"cf-connecting-ip": ip}
    if raw_auth is not None:
        headers["authorization"] = raw_auth
    elif user_id is not None:
        headers["authorization"] = f"Bearer {_token(user_id)}"
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/rides/active",
        "path_params": {},
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("198.51.100.1", 12345),
        "query_string": b"",
        "app": None,
    }
    return Request(scope)


# --------------------------------------------------------------------------
# Key function
# --------------------------------------------------------------------------


def test_authenticated_request_keys_by_user():
    assert get_user_or_ip_key(_fake_request(user_id="rider_abc")) == "user:rider_abc"


def test_different_users_behind_one_carrier_ip_get_separate_buckets():
    """The core CGNAT fix: same egress IP, different riders, separate budgets."""
    a = get_user_or_ip_key(_fake_request(user_id="rider_abc", ip=CARRIER_NAT_IP))
    b = get_user_or_ip_key(_fake_request(user_id="rider_xyz", ip=CARRIER_NAT_IP))
    assert a != b


def test_same_user_across_different_ips_shares_one_bucket():
    """A rider moving between Wi-Fi and cellular must not reset their budget —
    otherwise the limit is trivially evaded by toggling airplane mode."""
    a = get_user_or_ip_key(_fake_request(user_id="rider_abc", ip="203.0.113.5"))
    b = get_user_or_ip_key(_fake_request(user_id="rider_abc", ip="198.51.100.99"))
    assert a == b == "user:rider_abc"


def test_anonymous_request_falls_back_to_ip():
    key = get_user_or_ip_key(_fake_request(ip="203.0.113.5"))
    assert key == "ip:203.0.113.5"


@pytest.mark.parametrize(
    "raw_auth",
    [
        "",
        "Bearer",
        "Bearer not-a-jwt",
        "Basic dXNlcjpwYXNz",
        "Bearer eyJhbGciOiJIUzI1NiJ9.!!!corrupt!!!.sig",
    ],
)
def test_malformed_authorization_falls_back_to_ip(raw_auth):
    """A garbage token must not produce an empty/None key — AsyncLimiter treats
    a falsy key as 'skip the check', which would silently disable the limit."""
    key = get_user_or_ip_key(_fake_request(ip="203.0.113.5", raw_auth=raw_auth))
    assert key == "ip:203.0.113.5"


def test_token_without_user_claim_falls_back_to_ip():
    token = jwt.encode({"role": "rider"}, "irrelevant-test-secret", algorithm="HS256")
    key = get_user_or_ip_key(_fake_request(ip="203.0.113.5", raw_auth=f"Bearer {token}"))
    assert key == "ip:203.0.113.5"


def test_sub_claim_is_accepted_as_user_id():
    token = jwt.encode({"sub": "rider_from_sub"}, "irrelevant-test-secret", algorithm="HS256")
    key = get_user_or_ip_key(_fake_request(raw_auth=f"Bearer {token}"))
    assert key == "user:rider_from_sub"


# --------------------------------------------------------------------------
# Kill switch (rollback lever — see capacity-scaling.md §3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "False", "no"])
def test_kill_switch_reverts_to_ip_keying(monkeypatch, value):
    monkeypatch.setenv("RATE_LIMIT_USER_KEYING", value)
    key = get_user_or_ip_key(_fake_request(user_id="rider_abc", ip="203.0.113.5"))
    assert key == "ip:203.0.113.5"


@pytest.mark.parametrize("value", ["on", "", "anything-else"])
def test_user_keying_is_the_default(monkeypatch, value):
    monkeypatch.setenv("RATE_LIMIT_USER_KEYING", value)
    assert get_user_or_ip_key(_fake_request(user_id="rider_abc")) == "user:rider_abc"


def test_user_keying_active_when_env_unset(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_USER_KEYING", raising=False)
    assert get_user_or_ip_key(_fake_request(user_id="rider_abc")) == "user:rider_abc"


# --------------------------------------------------------------------------
# End-to-end through the real limiter machinery
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_rider_exhausting_the_limit_does_not_block_their_neighbour():
    """End-to-end proof using the real AsyncLimiter/storage, not just the key
    function: rider A burning the whole budget must leave rider B unaffected
    even though both share a carrier NAT egress IP. Under the old IP keying,
    B's request was refused here."""
    from limits.aio.storage import MemoryStorage

    from utils.async_limiter import AsyncLimiter

    limiter = AsyncLimiter(key_func=get_user_or_ip_key, storage=MemoryStorage())
    served = []

    @limiter.limit("1/minute", key_func=get_user_or_ip_key)
    async def _endpoint(request: Request):
        served.append(request)
        return "ok"

    await _endpoint(_fake_request(user_id="rider_a", ip=CARRIER_NAT_IP))
    assert len(served) == 1

    # Same rider, same budget — correctly refused.
    with pytest.raises(RateLimitExceeded):
        await _endpoint(_fake_request(user_id="rider_a", ip=CARRIER_NAT_IP))
    assert len(served) == 1

    # Different rider, SAME carrier IP — must still be served.
    await _endpoint(_fake_request(user_id="rider_b", ip=CARRIER_NAT_IP))
    assert len(served) == 2


@pytest.mark.asyncio
async def test_sos_is_not_blocked_by_a_stranger_on_the_same_carrier_ip():
    """Safety case. ride_action_limit guards POST /rides/{id}/emergency
    (routes/rides/safety.py:38). Under IP keying, riders who had spent the
    bucket on ordinary ride actions could exhaust it for an unrelated rider
    behind the same carrier NAT — whose SOS then 429'd. It must not."""
    from limits.aio.storage import MemoryStorage

    from utils.async_limiter import AsyncLimiter

    limiter = AsyncLimiter(key_func=get_user_or_ip_key, storage=MemoryStorage())
    served = []

    @limiter.limit("1/minute", key_func=get_user_or_ip_key)
    async def _emergency(request: Request):
        served.append(request)
        return "sos-dispatched"

    # A different rider on the same carrier IP burns the bucket first.
    await _emergency(_fake_request(user_id="noisy_rider", ip=CARRIER_NAT_IP))
    assert len(served) == 1

    # The rider in actual distress must still get through.
    await _emergency(_fake_request(user_id="rider_in_distress", ip=CARRIER_NAT_IP))
    assert len(served) == 2


def test_expired_token_still_keys_to_its_user():
    """The SOS route uses get_current_user_allow_expired, so a token that
    expired mid-trip still identifies the caller. The key func must agree —
    it decodes without verifying signature OR expiry — otherwise a rider whose
    token lapsed mid-ride would silently fall back into the shared carrier-IP
    bucket at exactly the moment they need the limit to be theirs alone."""
    expired = jwt.encode(
        {"user_id": "rider_expired", "exp": 1000000000},  # 2001
        "irrelevant-test-secret",
        algorithm="HS256",
    )
    assert get_user_or_ip_key(_fake_request(raw_auth=f"Bearer {expired}")) == "user:rider_expired"


@pytest.mark.asyncio
async def test_one_rider_cannot_evade_the_limit_by_changing_ip():
    """The limit must still bind: rotating IPs is the classic evasion that
    IP keying invited, and user keying closes it rather than widening it."""
    from limits.aio.storage import MemoryStorage

    from utils.async_limiter import AsyncLimiter

    limiter = AsyncLimiter(key_func=get_user_or_ip_key, storage=MemoryStorage())
    served = []

    @limiter.limit("1/minute", key_func=get_user_or_ip_key)
    async def _endpoint(request: Request):
        served.append(request)
        return "ok"

    await _endpoint(_fake_request(user_id="rider_a", ip="203.0.113.1"))
    with pytest.raises(RateLimitExceeded):
        await _endpoint(_fake_request(user_id="rider_a", ip="198.51.100.2"))
    assert len(served) == 1
