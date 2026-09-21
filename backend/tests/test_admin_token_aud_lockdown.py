"""Regressions for the security-audit P0/P1 fixes (June 2026 sweep).

Pins:
  - Legacy no-aud admin tokens are rejected: a crafted admin-001 payload
    without an ``aud`` claim must never pass _verify_admin_payload (it used
    to be accepted with zero DB verification).
  - Tokens with aud == JWT_AUD_ADMIN still verify (no regression for every
    properly-minted admin token).
  - _mint_admin_access_token no longer duplicates the email into the phone
    claim (PIPEDA data minimization).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException

import dependencies
from core.config import settings
from dependencies import JWT_AUD_ADMIN, _verify_admin_payload
from routes.admin.auth import _mint_admin_access_token


@pytest.fixture(autouse=True)
def _env_admin_token_version(monkeypatch):
    """Stub the DB-backed `admin-001` revocation counter (C8, PR #5602).

    These tests use `admin-001` to skip the staff-table lookup; that path now
    reads `env_admin_token_version` from the `settings` row and fails CLOSED on
    a read error by design, which 503s before the audience check under test.
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))


def _legacy_admin_payload() -> dict:
    """Admin-shaped payload WITHOUT an aud claim — the retired legacy form."""
    return {
        "user_id": "admin-001",
        "role": "super_admin",
        "email": "ops@spinr.ca",
        "jti": "tok-legacy",
        "token_version": 0,
    }


@pytest.mark.anyio
async def test_legacy_no_aud_admin_token_rejected():
    """A no-aud token with admin role+email claims must 401, not verify."""
    with pytest.raises(HTTPException) as exc:
        await _verify_admin_payload(_legacy_admin_payload())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_TOKEN_AUDIENCE"


@pytest.mark.anyio
async def test_admin_aud_token_still_verifies(monkeypatch):
    """Properly-minted admin tokens (aud claim present) keep working."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value=None))
    payload = {**_legacy_admin_payload(), "aud": JWT_AUD_ADMIN}
    user = await _verify_admin_payload(payload)
    assert user is not None
    assert user["id"] == "admin-001"
    assert user["role"] == "super_admin"


@pytest.mark.anyio
async def test_no_aud_rider_payload_returns_none():
    """A plain rider payload (no role/email) is not admin — returns None so
    the caller falls through to the mobile verification path."""
    result = await _verify_admin_payload({"user_id": "rider-1"})
    assert result is None


def test_minted_admin_token_has_empty_phone_claim():
    """PIPEDA: the phone claim must be empty, not a duplicate of the email."""
    token, _ = _mint_admin_access_token(
        user_id="staff-1",
        email="ops@spinr.ca",
        role="admin",
        modules=["dashboard"],
        token_version=0,
    )
    payload = pyjwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.ALGORITHM],
        audience=JWT_AUD_ADMIN,
    )
    assert payload["phone"] == ""
    assert payload["aud"] == JWT_AUD_ADMIN
