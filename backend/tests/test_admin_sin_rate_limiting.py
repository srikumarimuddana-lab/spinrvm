"""Rate-limit tests for ACTION_ITEMS.md D8: SIN-touching admin endpoints.

Covers the four endpoints that were previously unlimited:
  - POST /admin/drivers/{id}/reveal-sin   (admin_sin_reveal_limit, 10/hour)
  - POST /admin/drivers/{id}/update-sin   (admin_sin_update_limit, 10/hour)
  - POST /admin/tax-ids/import/validate   (tax_id_import_validate_limit, 30/hour)
  - POST /admin/tax-ids/import/commit     (tax_id_import_commit_limit, 10/hour)

Follows the same pattern as
tests/test_admin_driver_import.py::test_commit_limiter_blocks_after_configured_rate —
exercise the real AsyncLimiter/storage mechanics at the same rate the
production limiter uses, against a throwaway limiter/storage pair rather than
the shared default_limiter (which conftest's autouse `reset_rate_limiters`
fixture disables globally for every other test in the suite).
"""

import jwt
import pytest
from limits.aio.storage import MemoryStorage
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from utils.async_limiter import AsyncLimiter
from utils.rate_limiter import get_user_or_ip_key


def _admin_request(user_id: str = "admin-1", path: str = "/api/admin/drivers/drv-1/reveal-sin") -> Request:
    """Fake request carrying a bearer token with a `user_id` claim, mirroring
    the shape admin JWTs actually have (routes/admin/auth.py
    `_mint_admin_access_token`). Signature is irrelevant here —
    get_user_or_ip_key/_extract_unverified_user_id decodes without verifying,
    exactly as it does for the real (already-authenticated-by-a-separate-
    dependency) request.
    """
    token = jwt.encode(
        {"user_id": user_id, "role": "super_admin"}, "unused-test-secret-not-real-jwt-key", algorithm="HS256"
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "path_params": {},
        "headers": [
            (b"cf-connecting-ip", b"203.0.113.10"),
            (b"authorization", f"Bearer {token}".encode()),
        ],
        "client": ("203.0.113.10", 12345),
        "query_string": b"",
        "app": None,
    }
    return Request(scope)


async def _drain_and_assert_blocked(limit_value: str, n_allowed: int, path: str) -> None:
    limiter = AsyncLimiter(key_func=get_user_or_ip_key, storage=MemoryStorage())
    calls = []

    @limiter.limit(limit_value)
    async def _fake_endpoint(request: Request):
        calls.append(request)
        return "ok"

    for _ in range(n_allowed):
        await _fake_endpoint(_admin_request(path=path))
    assert len(calls) == n_allowed

    with pytest.raises(RateLimitExceeded):
        await _fake_endpoint(_admin_request(path=path))
    assert len(calls) == n_allowed  # the extra call was blocked, not just uncounted


@pytest.mark.asyncio
async def test_reveal_sin_limiter_blocks_after_10_per_hour():
    """admin_sin_reveal_limit is defined as default_limiter.limit("10/hour",
    key_func=get_user_or_ip_key) — proves both the rate and the key_func
    choice against real AsyncLimiter mechanics."""
    await _drain_and_assert_blocked("10/hour", 10, "/api/admin/drivers/drv-1/reveal-sin")


@pytest.mark.asyncio
async def test_update_sin_limiter_blocks_after_10_per_hour():
    """admin_sin_update_limit — same 10/hour bound as reveal, same rationale
    (D8's own suggested figure)."""
    await _drain_and_assert_blocked("10/hour", 10, "/api/admin/drivers/drv-1/update-sin")


@pytest.mark.asyncio
async def test_tax_id_import_validate_limiter_blocks_after_30_per_hour():
    """tax_id_import_validate_limit — read-only dry-run, looser than commit,
    matching the existing validate/commit asymmetry (booking_import_*_limit,
    data_transfer_import_*_limit, driver_import_commit_limit)."""
    await _drain_and_assert_blocked("30/hour", 30, "/api/admin/tax-ids/import/validate")


@pytest.mark.asyncio
async def test_tax_id_import_commit_limiter_blocks_after_10_per_hour():
    """tax_id_import_commit_limit — the write path, tighter than validate."""
    await _drain_and_assert_blocked("10/hour", 10, "/api/admin/tax-ids/import/commit")


@pytest.mark.asyncio
async def test_sin_limits_are_keyed_per_admin_not_per_ip():
    """D8 explicitly asks for a *per-admin* limit. Every other admin_* limiter
    in rate_limiter.py defaults to IP keying, which would let two
    super_admins behind the same office/VPN egress IP silently share (and
    exhaust) one bucket. Proves two different admin JWTs from the SAME
    source IP get independent buckets under get_user_or_ip_key."""
    limiter = AsyncLimiter(key_func=get_user_or_ip_key, storage=MemoryStorage())
    calls = []

    @limiter.limit("10/hour")
    async def _fake_reveal(request: Request):
        calls.append(request)
        return "ok"

    # admin-1 exhausts their own bucket.
    for _ in range(10):
        await _fake_reveal(_admin_request(user_id="admin-1"))
    with pytest.raises(RateLimitExceeded):
        await _fake_reveal(_admin_request(user_id="admin-1"))

    # admin-2, same source IP, untouched bucket — must still succeed.
    await _fake_reveal(_admin_request(user_id="admin-2"))
    assert len(calls) == 11  # 10 from admin-1 + 1 from admin-2
