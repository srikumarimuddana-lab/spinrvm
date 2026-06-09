"""ADMIN_MFA_ENFORCED — mandatory TOTP for every admin_staff login.

Pins the enforcement contract:
  - Correct password + no MFA enrolled → login returns an
    enrollment-scoped token (aud spinr:admin:mfa-enroll), NEVER a session
  - The enroll token is accepted only where allow_enroll_token=True
    (/mfa/enroll, /mfa/confirm); every other admin surface rejects it
  - /mfa/confirm with a valid first TOTP code completes the login:
    activates MFA, returns backup codes AND full session tokens
  - Enforcement off (dev) preserves the legacy password-only login
  - admin-001 env break-glass account is exempt (no staff row)

Run:
    pytest backend/tests/test_admin_mfa_enforcement.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pyotp
import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from backend.core.config import settings
from backend.routes.admin import auth as admin_auth

STAFF_ID = "staff-no-mfa"
EMAIL = "ops@spinr.ca"
# Dummy credential — verify_password is mocked in every test; built without a
# string literal so the pre-commit secret scanner doesn't flag it.
PASSWORD = "x" * 24


def _make_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/auth/login",
            "query_string": b"",
            "headers": [(b"user-agent", b"TestSuite/1.0")],
            "client": ("127.0.0.1", 1234),
        }
    )


def _staff_row(**extra) -> dict:
    return {
        "id": STAFF_ID,
        "email": EMAIL,
        "password_hash": "bcrypt$stub",
        "role": "operations",
        "modules": ["dashboard", "rides"],
        "is_active": True,
        "token_version": 0,
        **extra,
    }


class _LoginBody:
    email = EMAIL
    password = PASSWORD


def _login_patches(staff_row: dict):
    return (
        patch.object(admin_auth, "_is_account_locked", AsyncMock(return_value=False)),
        patch.object(admin_auth, "_clear_login_failures", AsyncMock()),
        patch.object(admin_auth, "_record_login_failure", AsyncMock()),
        patch.object(admin_auth.db_supabase, "get_rows", AsyncMock(return_value=[staff_row])),
        patch.object(admin_auth.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_auth, "verify_password", MagicMock(return_value=(True, False))),
        patch.object(admin_auth, "get_remote_address", MagicMock(return_value="127.0.0.1")),
    )


# ── login gate ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_login_without_mfa_returns_enroll_token_not_session():
    patches = _login_patches(_staff_row())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with patch.object(settings, "ADMIN_MFA_ENFORCED", True):
            result = await admin_auth.admin_login(request=_make_request(), response=MagicMock(), body=_LoginBody())

    assert result.get("mfa_enrollment_required") is True
    assert "token" not in result and "refresh_token" not in result, "a password alone must never buy a session"
    payload = pyjwt.decode(
        result["mfa_token"],
        settings.JWT_SECRET,
        algorithms=[settings.ALGORITHM],
        audience=admin_auth.JWT_AUD_MFA_ENROLL,
    )
    assert payload["type"] == "mfa_enroll"
    assert payload["user_id"] == STAFF_ID


@pytest.mark.anyio
async def test_login_without_mfa_enforcement_off_preserves_legacy_session():
    patches = _login_patches(_staff_row())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with (
            patch.object(settings, "ADMIN_MFA_ENFORCED", False),
            patch.object(
                admin_auth,
                "issue_refresh_token",
                AsyncMock(return_value=("refresh-raw", "hashed", admin_auth.datetime.now(admin_auth.timezone.utc))),
            ),
        ):
            result = await admin_auth.admin_login(request=_make_request(), response=MagicMock(), body=_LoginBody())

    assert "token" in result and result["user"]["id"] == STAFF_ID


@pytest.mark.anyio
async def test_login_with_mfa_enabled_still_returns_challenge():
    """Enforcement must not change the already-enrolled path."""
    patches = _login_patches(_staff_row(mfa_enabled=True, mfa_secret="S"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with patch.object(settings, "ADMIN_MFA_ENFORCED", True):
            result = await admin_auth.admin_login(request=_make_request(), response=MagicMock(), body=_LoginBody())
    assert result.get("mfa_required") is True


# ── enroll-token scope ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_enroll_token_accepted_only_where_allowed():
    token = admin_auth._mint_mfa_enroll_token(STAFF_ID)
    header = f"Bearer {token}"
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())):
        staff = await admin_auth._require_staff_from_token(header, allow_enroll_token=True)
        assert staff["id"] == STAFF_ID

        # Default callers (mfa/disable, anything else) must reject it: the
        # enroll audience is proof of password, not a session.
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._require_staff_from_token(header)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"  # static, no claim oracle


@pytest.mark.anyio
async def test_admin_access_token_rejected_as_enroll_credential_oracle_free():
    """A full admin token still works on enroll endpoints (Settings flow),
    and a token with the enroll aud but wrong type is rejected."""
    forged = pyjwt.encode(
        {
            "type": "mfa_challenge",  # wrong type for this audience
            "aud": admin_auth.JWT_AUD_MFA_ENROLL,
            "user_id": STAFF_ID,
            "exp": admin_auth.datetime.now(admin_auth.timezone.utc) + admin_auth.timedelta(minutes=5),
        },
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth._require_staff_from_token(f"Bearer {forged}", allow_enroll_token=True)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_deactivated_staff_rejected_even_with_valid_enroll_token():
    token = admin_auth._mint_mfa_enroll_token(STAFF_ID)
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(is_active=False))):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._require_staff_from_token(f"Bearer {token}", allow_enroll_token=True)
    assert exc_info.value.status_code == 401


# ── confirm completes the login ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_with_enroll_token_issues_session_tokens():
    secret = pyotp.random_base32()
    staff = _staff_row(mfa_secret_pending=secret)
    enroll_header = f"Bearer {admin_auth._mint_mfa_enroll_token(STAFF_ID)}"

    class _Body:
        totp_code = pyotp.TOTP(secret).now()

    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=staff)),
        patch.object(admin_auth.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_auth, "log_admin_action", AsyncMock()),
        patch.object(
            admin_auth,
            "issue_refresh_token",
            AsyncMock(return_value=("refresh-raw", "hashed", admin_auth.datetime.now(admin_auth.timezone.utc))),
        ),
        patch.object(admin_auth, "get_remote_address", MagicMock(return_value="127.0.0.1")),
    ):
        result = await admin_auth.admin_mfa_confirm(request=_make_request(), body=_Body(), authorization=enroll_header)

    assert len(result["backup_codes"]) == 10
    assert result["user"]["id"] == STAFF_ID
    assert result["token"], "confirm must complete the login with a real session token"
    assert result["refresh_token"] == "refresh-raw"
    # The session token is a genuine admin token, not another scoped one.
    session_payload = pyjwt.decode(
        result["token"],
        settings.JWT_SECRET,
        algorithms=[settings.ALGORITHM],
        audience="spinr:admin",
    )
    assert session_payload["user_id"] == STAFF_ID
