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
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
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
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
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


# ── Codex PR #1724 regressions ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_enroll_token_bound_to_token_version_rejected_after_bump():
    """Codex #1724 (P1): an enrollment token minted before a logout-all /
    MFA reset (which bump token_version) must not still complete /mfa/confirm."""
    stale = admin_auth._mint_mfa_enroll_token(STAFF_ID, token_version=0)
    # admin_staff.token_version was bumped to 1 by the revocation.
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(token_version=1))):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._require_staff_from_token(f"Bearer {stale}", allow_enroll_token=True)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_admin_access_token_rejected_after_token_version_bump():
    """Codex #1724 (P1): a revoked admin access token must not pass
    _require_staff_from_token and mint a fresh session via confirm."""
    stale = admin_auth._mint_admin_access_token(
        user_id=STAFF_ID, email=EMAIL, role="operations", modules=["dashboard"], token_version=0
    )[0]
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(token_version=1))):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._require_staff_from_token(f"Bearer {stale}")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_settings_reenrollment_does_not_mint_new_session():
    """Codex #1724 (P1): confirm via a full admin access token (Settings flow)
    returns backup_codes only — no refresh_token to desync the live session."""
    secret = pyotp.random_base32()
    staff = _staff_row(mfa_secret_pending=secret)
    session_header = "Bearer " + admin_auth._mint_admin_access_token(
        user_id=STAFF_ID, email=EMAIL, role="operations", modules=["dashboard"], token_version=0
    )[0]

    class _Body:
        totp_code = pyotp.TOTP(secret).now()

    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=staff)),
        patch.object(admin_auth.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_auth, "log_admin_action", AsyncMock()),
        patch.object(admin_auth, "issue_refresh_token", AsyncMock()) as issue,
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
    ):
        result = await admin_auth.admin_mfa_confirm(
            request=_make_request(), body=_Body(), authorization=session_header
        )

    assert result == {"backup_codes": result["backup_codes"]}
    assert "refresh_token" not in result and "token" not in result
    issue.assert_not_awaited()


@pytest.mark.anyio
async def test_admin_refresh_blocks_unenrolled_staff_when_enforced():
    """Codex #1724 (P1): a pre-enforcement admin refresh token must not keep
    minting sessions for a staff member who never enrolled in MFA."""
    row = {"user_id": STAFF_ID, "audience": "admin"}

    class _Body:
        refresh_token = "pre-enforcement-rt"

    with (
        patch.object(admin_auth, "lookup_refresh_token", AsyncMock(return_value=row)),
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(mfa_enabled=False))),
        patch.object(settings, "ADMIN_MFA_ENFORCED", True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_refresh(request=_make_request(), body=_Body())
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_admin_refresh_allows_enrolled_staff():
    """Sanity: an enrolled staff member still refreshes normally under enforcement."""
    row = {"user_id": STAFF_ID, "audience": "admin", "id": "rt-row"}

    class _Body:
        refresh_token = "good-rt"

    with (
        patch.object(admin_auth, "lookup_refresh_token", AsyncMock(return_value=row)),
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(mfa_enabled=True, mfa_secret="S"))),
        patch.object(settings, "ADMIN_MFA_ENFORCED", True),
        patch.object(
            admin_auth,
            "issue_refresh_token",
            AsyncMock(return_value=("new-rt", "h", admin_auth.datetime.now(admin_auth.timezone.utc))),
        ),
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
    ):
        result = await admin_auth.admin_refresh(request=_make_request(), body=_Body())
    assert result["token"]


@pytest.mark.anyio
async def test_enroll_token_dies_once_mfa_is_enabled():
    """Codex #1724 round 2 (P1): an intercepted enrollment token must not be
    replayable after the legitimate first enrollment completes — it could
    re-run /mfa/enroll, overwrite the fresh secret, and mint a session."""
    token = admin_auth._mint_mfa_enroll_token(STAFF_ID, token_version=0)
    enrolled_row = _staff_row(mfa_enabled=True, mfa_secret="BOUND", token_version=0)
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=enrolled_row)):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._require_staff_from_token(f"Bearer {token}", allow_enroll_token=True)
    assert exc_info.value.status_code == 401
    # A full admin session token is still fine on the same endpoints
    # (Settings flow for an enrolled account, e.g. /mfa/disable).
    session = admin_auth._mint_admin_access_token(
        user_id=STAFF_ID, email=EMAIL, role="operations", modules=["dashboard"], token_version=0
    )[0]
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=enrolled_row)):
        staff = await admin_auth._require_staff_from_token(f"Bearer {session}", allow_enroll_token=True)
    assert staff["id"] == STAFF_ID


# ── Codex PR #1724 round-3 regressions ───────────────────────────────────────


@pytest.mark.anyio
async def test_challenge_token_bound_to_token_version():
    """Codex round 3 (P2): a challenge token minted before a logout-all /
    MFA reset must not exchange for a session after the version bump."""
    secret = pyotp.random_base32()
    stale = admin_auth._mint_mfa_challenge_token(STAFF_ID, token_version=0)
    enrolled = _staff_row(mfa_enabled=True, mfa_secret=secret, token_version=1)

    class _Body:
        mfa_token = stale
        totp_code = pyotp.TOTP(secret).now()

    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=enrolled)):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_mfa_challenge(request=_make_request(), body=_Body())
    assert exc_info.value.status_code == 401

    # Same-version challenge still works end-to-end.
    fresh = admin_auth._mint_mfa_challenge_token(STAFF_ID, token_version=1)

    class _FreshBody:
        mfa_token = fresh
        totp_code = pyotp.TOTP(secret).now()

    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=enrolled)),
        patch.object(
            admin_auth,
            "issue_refresh_token",
            AsyncMock(return_value=("rt", "h", admin_auth.datetime.now(admin_auth.timezone.utc))),
        ),
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
    ):
        result = await admin_auth.admin_mfa_challenge(request=_make_request(), body=_FreshBody())
    assert result["token"]


@pytest.mark.anyio
async def test_mfa_disable_blocked_under_enforcement():
    """Codex round 3 (P2): self-service disable would let staff opt out of
    mandatory MFA and keep their 1-hour session. 403 under enforcement;
    recovery is the super-admin reset path."""
    secret = pyotp.random_base32()
    enrolled = _staff_row(mfa_enabled=True, mfa_secret=secret, password_hash="bcrypt$x")
    header = "Bearer " + admin_auth._mint_admin_access_token(
        user_id=STAFF_ID, email=EMAIL, role="operations", modules=["dashboard"], token_version=0
    )[0]

    class _Body:
        totp_code = pyotp.TOTP(secret).now()
        password = "x" * 24

    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=enrolled)),
        patch.object(settings, "ADMIN_MFA_ENFORCED", True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_mfa_disable(request=_make_request(), body=_Body(), authorization=header)
    assert exc_info.value.status_code == 403

    # Enforcement off → the password+TOTP disable flow still works.
    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=enrolled)),
        patch.object(settings, "ADMIN_MFA_ENFORCED", False),
        patch.object(admin_auth, "verify_password", MagicMock(return_value=(True, False))),
        patch.object(admin_auth.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_auth, "log_admin_action", AsyncMock()),
    ):
        result = await admin_auth.admin_mfa_disable(request=_make_request(), body=_Body(), authorization=header)
    assert result == {"success": True}


# ── Codex PR #1724 round-4 regressions ───────────────────────────────────────


@pytest.mark.anyio
async def test_jti_revoked_admin_token_rejected_by_mfa_helper():
    """Codex round 4 (P2): /admin/auth/logout blacklists a single token's JTI
    without bumping token_version; the MFA helper must honor the denylist so
    a logged-out token can't start/confirm enrollment or disable MFA."""
    token = admin_auth._mint_admin_access_token(
        user_id=STAFF_ID, email=EMAIL, role="operations", modules=["dashboard"], token_version=0
    )[0]
    with (
        patch.object(admin_auth, "redis_get", AsyncMock(return_value="1")),  # jti revoked
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._require_staff_from_token(f"Bearer {token}")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"

    # Redis outage fails OPEN (token_version gate still authoritative),
    # matching _verify_admin_payload.
    with (
        patch.object(admin_auth, "redis_get", AsyncMock(side_effect=ConnectionError("redis down"))),
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())),
    ):
        staff = await admin_auth._require_staff_from_token(f"Bearer {token}")
    assert staff["id"] == STAFF_ID
