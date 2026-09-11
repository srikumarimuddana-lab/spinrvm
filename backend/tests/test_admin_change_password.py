"""Coverage for POST /admin/auth/change-password
(routes/admin/auth.py::change_password).

Admin RBAC/security audit finding W5 (docs/audit/2026-09-10-admin-portal-
security-rbac-audit.md): this endpoint used to resolve the caller with a
bare ``jwt.decode()`` instead of the shared ``_require_staff_from_token``
helper every other authenticated admin action in this file goes through —
skipping the ``is_active`` check, the per-JTI ``admin:revoked:{jti}``
denylist, and the ``token_version`` revocation gate (the field
``/admin/auth/logout-all`` bumps), and never re-verifying TOTP even when
the account has MFA enrolled. No test file exercised this endpoint at all
before this one (confirmed: absent from every sibling admin-auth test
file's own "what I cover" docstring, e.g. test_admin_auth_coverage_gap.py).

Run:
    pytest backend/tests/test_admin_change_password.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pyotp
import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from backend.routes.admin import auth as admin_auth

STAFF_ID = "staff-change-pw"
_STRONG_TEST_PW = "Str0ng-Enough-" + "Passw0rd!"  # synthetic, not a real credential


def _make_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/auth/change-password",
            "query_string": b"",
            "headers": [(b"user-agent", b"TestSuite/1.0")],
            "client": ("127.0.0.1", 1234),
        }
    )


def _staff_row(**overrides) -> dict:
    row = {
        "id": STAFF_ID,
        "email": "cp@spinr.ca",
        "role": "operations",
        "modules": ["dashboard"],
        "is_active": True,
        "token_version": 0,
        "mfa_enabled": False,
        "password_hash": admin_auth.hash_password("current-Passw0rd!"),
    }
    row.update(overrides)
    return row


def _body(current="current-Passw0rd!", new=_STRONG_TEST_PW, totp_code=None):
    return admin_auth.ChangePasswordRequest(current_password=current, new_password=new, totp_code=totp_code)


def _token(role="operations", token_version=0):
    token, _ = admin_auth._mint_admin_access_token(STAFF_ID, "cp@spinr.ca", role, ["dashboard"], token_version)
    return token


# ---------------------------------------------------------------------------
# Caller resolution — the actual W5 fix
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rejects_missing_authorization():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.change_password(request=_make_request(), body=_body(), authorization=None)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_rejects_malformed_token():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.change_password(request=_make_request(), body=_body(), authorization="Bearer not-a-jwt")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_super_admin_env_account_blocked_with_specific_message():
    token, _ = admin_auth._mint_admin_access_token("admin-001", "a@b.com", "super_admin", [], 0)
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.change_password(request=_make_request(), body=_body(), authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 400
    # Endpoint-specific message (via the new admin001_detail param), not the
    # generic MFA-flavored default _require_staff_from_token's other callers use.
    assert "ADMIN_PASSWORD" in exc_info.value.detail


@pytest.mark.anyio
async def test_staff_not_found():
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(request=_make_request(), body=_body(), authorization=f"Bearer {_token()}")
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_inactive_account_rejected():
    """The bare-decode bug this fix closes: is_active was never checked before."""
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(is_active=False))):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(request=_make_request(), body=_body(), authorization=f"Bearer {_token()}")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_revoked_jti_rejected():
    """The bare-decode bug this fix closes: a single-token /admin/auth/logout
    revocation (admin:revoked:{jti}, no token_version bump) was never
    checked before — a logged-out token could still change the password."""
    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())),
        patch.object(admin_auth, "redis_get", AsyncMock(return_value="1")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(request=_make_request(), body=_body(), authorization=f"Bearer {_token()}")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_stale_token_version_rejected():
    """The core bug this fix closes: /admin/auth/logout-all bumps
    token_version to force-invalidate every session — a still-unexpired
    (<=1h TTL) captured access token minted before that bump was never
    checked against the DB's current token_version before, defeating the
    revocation guarantee for this one endpoint."""
    token = _token(token_version=0)
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(token_version=1))):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(request=_make_request(), body=_body(), authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wrong_current_password_rejected():
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(
                request=_make_request(), body=_body(current="wrong"), authorization=f"Bearer {_token()}"
            )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_new_password_too_short_rejected():
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(
                request=_make_request(), body=_body(new="short"), authorization=f"Bearer {_token()}"
            )
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# TOTP re-verification (new: mirrors admin_mfa_disable's posture)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mfa_enrolled_missing_totp_code_rejected():
    with patch.object(
        admin_auth.db,
        "find_one",
        AsyncMock(return_value=_staff_row(mfa_enabled=True, mfa_secret=pyotp.random_base32())),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(request=_make_request(), body=_body(), authorization=f"Bearer {_token()}")
    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_mfa_enrolled_wrong_totp_code_rejected():
    secret = pyotp.random_base32()
    with patch.object(
        admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(mfa_enabled=True, mfa_secret=secret))
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.change_password(
                request=_make_request(), body=_body(totp_code="000000"), authorization=f"Bearer {_token()}"
            )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_mfa_enrolled_correct_totp_code_succeeds():
    secret = pyotp.random_base32()
    with (
        patch.object(
            admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(mfa_enabled=True, mfa_secret=secret))
        ),
        patch.object(admin_auth.db, "update_one", AsyncMock()) as update_mock,
    ):
        result = await admin_auth.change_password(
            request=_make_request(),
            body=_body(totp_code=pyotp.TOTP(secret).now()),
            authorization=f"Bearer {_token()}",
        )
    assert result["success"] is True
    update_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Happy path (no MFA)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_mfa_change_succeeds_and_hashes_new_password():
    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())),
        patch.object(admin_auth.db, "update_one", AsyncMock()) as update_mock,
    ):
        result = await admin_auth.change_password(
            request=_make_request(), body=_body(), authorization=f"Bearer {_token()}"
        )
    assert result == {"success": True, "message": "Password changed successfully"}
    update_mock.assert_awaited_once()
    args, _ = update_mock.call_args
    assert args[0] == "admin_staff"
    assert args[1] == {"id": STAFF_ID}
    new_hash = args[2]["$set"]["password_hash"]
    ok, _ = admin_auth.verify_password(_STRONG_TEST_PW, new_hash)
    assert ok is True
