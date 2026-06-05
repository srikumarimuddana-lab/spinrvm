"""Regression: admin JTI-revocation denylist fails OPEN on a Redis outage.

A Redis (Upstash) blip must not lock every admin out of the live dashboard —
the per-JTI denylist is a fast-path, and the authoritative revocation
(/auth/logout-all) is enforced via the DB token_version check, not Redis. But
a genuine revocation (Redis reachable, key present) must still be blocked.

Uses the admin-001 bootstrap identity so the staff-table DB lookup is skipped
and the test exercises only the revocation branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import dependencies
from dependencies import JWT_AUD_ADMIN, _verify_admin_payload


def _admin_payload() -> dict:
    return {
        "user_id": "admin-001",
        "role": "admin",
        "email": "ops@spinr.ca",
        "aud": JWT_AUD_ADMIN,
        "jti": "tok-1",
        "token_version": 0,
    }


@pytest.mark.anyio
async def test_fails_open_when_redis_unavailable(monkeypatch):
    """redis_get raising (Upstash down) must NOT reject a valid admin token."""
    monkeypatch.setattr(
        dependencies, "redis_get", AsyncMock(side_effect=RuntimeError("Upstash unreachable"))
    )
    user = await _verify_admin_payload(_admin_payload())
    assert user is not None
    assert user["id"] == "admin-001"
    assert user["role"] == "admin"


@pytest.mark.anyio
async def test_blocks_when_redis_reports_revoked(monkeypatch):
    """When Redis IS reachable and the JTI is on the denylist, still 401."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value="1"))
    with pytest.raises(HTTPException) as exc:
        await _verify_admin_payload(_admin_payload())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_TOKEN_REVOKED"


@pytest.mark.anyio
async def test_allows_when_redis_reports_not_revoked(monkeypatch):
    """Healthy Redis, JTI absent from denylist → token passes."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value=None))
    user = await _verify_admin_payload(_admin_payload())
    assert user is not None
    assert user["id"] == "admin-001"
