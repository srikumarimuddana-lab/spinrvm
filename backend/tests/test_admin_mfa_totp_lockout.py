"""Per-account TOTP failure lockout on /mfa/challenge.

The per-IP SlowAPI limit (10/min) alone is bypassable via IP rotation,
making 6-digit TOTP codes brute-forceable. Pins:
  - 5 recorded failures lock the account: even a VALID code gets 429
  - an invalid code records a failure against the account
  - a successful exchange clears the failure counter

Run:
    pytest backend/tests/test_admin_mfa_totp_lockout.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from backend.routes.admin import auth as admin_auth

STAFF_ID = "staff-totp-lockout"


def _make_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/auth/mfa/challenge",
            "query_string": b"",
            "headers": [(b"user-agent", b"TestSuite/1.0")],
            "client": ("127.0.0.1", 1234),
        }
    )


def _staff_row(secret: str) -> dict:
    return {
        "id": STAFF_ID,
        "email": "ops@spinr.ca",
        "role": "operations",
        "modules": ["dashboard"],
        "is_active": True,
        "token_version": 0,
        "mfa_enabled": True,
        "mfa_secret": secret,
        "mfa_backup_codes": [],
    }


@pytest.mark.anyio
async def test_locked_account_rejected_even_with_valid_code():
    secret = pyotp.random_base32()
    token = admin_auth._mint_mfa_challenge_token(STAFF_ID, token_version=0)

    class _Body:
        mfa_token = token
        totp_code = pyotp.TOTP(secret).now()  # VALID code

    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(secret))),
        patch.object(admin_auth, "_is_totp_locked", AsyncMock(return_value=True)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_mfa_challenge(request=_make_request(), body=_Body())
    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_invalid_code_records_account_failure():
    secret = pyotp.random_base32()
    token = admin_auth._mint_mfa_challenge_token(STAFF_ID, token_version=0)

    class _Body:
        mfa_token = token
        totp_code = "000000"

    record_mock = AsyncMock()
    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(secret))),
        patch.object(admin_auth, "_is_totp_locked", AsyncMock(return_value=False)),
        patch.object(admin_auth, "_record_totp_failure", record_mock),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_mfa_challenge(request=_make_request(), body=_Body())
    assert exc_info.value.status_code == 401
    record_mock.assert_awaited_once_with(STAFF_ID)


@pytest.mark.anyio
async def test_successful_exchange_clears_failures():
    secret = pyotp.random_base32()
    token = admin_auth._mint_mfa_challenge_token(STAFF_ID, token_version=0)

    class _Body:
        mfa_token = token
        totp_code = pyotp.TOTP(secret).now()

    clear_mock = AsyncMock()
    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(secret))),
        patch.object(admin_auth, "_is_totp_locked", AsyncMock(return_value=False)),
        patch.object(admin_auth, "_clear_totp_failures", clear_mock),
        patch.object(
            admin_auth,
            "issue_refresh_token",
            AsyncMock(return_value=("rt", "h", admin_auth.datetime.now(admin_auth.timezone.utc))),
        ),
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
    ):
        result = await admin_auth.admin_mfa_challenge(request=_make_request(), body=_Body())
    assert result["token"]
    clear_mock.assert_awaited_once_with(STAFF_ID)
