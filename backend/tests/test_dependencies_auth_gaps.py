"""dependencies/__init__.py — coverage for previously-untested branches.

get_current_user's Firebase-token success path (uid lookup, phone
fallback, session revocation, driver caching, account-active
enforcement) had zero direct coverage — existing tests only exercise
the "not a Firebase token, fall through to JWT" branch. This file also
covers _verify_admin_payload's staff-inactive / token-version /
idle-timeout branches, JWT-path DB-error propagation, and the
get_current_user_allow_expired grace-window's remaining branches.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

pytestmark = pytest.mark.asyncio


def _creds(token: str = "firebase-or-jwt-token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ─────────────────────────────────────────────────────────────────────────────
# get_current_user — Firebase success path
# ─────────────────────────────────────────────────────────────────────────────


async def test_firebase_rider_app_id_not_configured_raises_503():
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value={"uid": "fb-1"}),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", ""),
    ):
        from dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_creds())
    assert exc.value.status_code == 503


async def test_firebase_wrong_audience_rejected():
    payload = {"uid": "fb-1", "aud": "some-other-app"}
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
    ):
        from dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_creds())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_TOKEN_AUDIENCE"


async def test_firebase_uid_lookup_hit_returns_user():
    payload = {"uid": "fb-1", "aud": "rider-app"}
    user_row = {"id": "fb-1", "phone": "+1", "sessions_invalid_before": None}
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user_row))),
        patch("dependencies.db_supabase.get_driver_by_user_id_cached", AsyncMock(return_value=None)),
    ):
        from dependencies import get_current_user

        user = await get_current_user(_creds())

    assert user["id"] == "fb-1"
    assert user["is_driver"] is False
    assert "_admin_verified" not in user


async def test_firebase_uid_miss_falls_back_to_phone_lookup():
    payload = {"uid": "fb-missing", "aud": "rider-app", "phone_number": "+15551234567"}
    user_row = {"id": "u-by-phone", "phone": "+15551234567", "sessions_invalid_before": None}
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("dependencies.db_supabase.get_user_by_phone", AsyncMock(return_value=dict(user_row))) as m_phone,
        patch("dependencies.db_supabase.get_driver_by_user_id_cached", AsyncMock(return_value=None)),
    ):
        from dependencies import get_current_user

        user = await get_current_user(_creds())

    assert user["id"] == "u-by-phone"
    m_phone.assert_awaited_once_with("+15551234567")


async def test_firebase_both_lookups_miss_raises_service_unavailable():
    """CLAUDE.md: never auto-create on a None lookup — fail closed with 503
    so the client retries instead of forking a phantom account."""
    payload = {"uid": "fb-missing", "aud": "rider-app", "phone_number": "+15551234567"}
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("dependencies.db_supabase.get_user_by_phone", AsyncMock(return_value=None)),
    ):
        from dependencies import get_current_user
        from utils.error_handling import ServiceUnavailableException

        with pytest.raises(ServiceUnavailableException):
            await get_current_user(_creds())


async def test_firebase_session_revoked_by_logout_all_returns_401():
    future_auth_time = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
    payload = {"uid": "fb-1", "aud": "rider-app", "auth_time": future_auth_time}
    # sessions_invalid_before is AFTER auth_time → the sign-in predates the watermark.
    watermark = (datetime.now(timezone.utc)).isoformat()
    user_row = {"id": "fb-1", "phone": "+1", "sessions_invalid_before": watermark}
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user_row))),
    ):
        from dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_creds())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_SESSION_REVOKED"


async def test_firebase_driver_flag_set_true_when_driver_row_exists():
    payload = {"uid": "fb-1", "aud": "rider-app"}
    user_row = {"id": "fb-1", "phone": "+1", "sessions_invalid_before": None}
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user_row))),
        patch("dependencies.db_supabase.get_driver_by_user_id_cached", AsyncMock(return_value={"id": "d1"})),
    ):
        from dependencies import get_current_user

        user = await get_current_user(_creds())

    assert user["is_driver"] is True


async def test_firebase_deleted_account_rejected():
    payload = {"uid": "fb-1", "aud": "rider-app"}
    user_row = {
        "id": "fb-1",
        "phone": "+1",
        "sessions_invalid_before": None,
        "status": "pending_deletion",
    }
    with (
        patch("dependencies.firebase_auth.verify_id_token", return_value=payload),
        patch("dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user_row))),
        patch("dependencies.db_supabase.get_driver_by_user_id_cached", AsyncMock(return_value=None)),
    ):
        from dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_creds())
    assert exc.value.status_code == 403
    assert exc.value.detail == "ERR_ACCOUNT_DELETED"


# ─────────────────────────────────────────────────────────────────────────────
# _verify_admin_payload — staff-inactive / token-version / idle-timeout
# ─────────────────────────────────────────────────────────────────────────────


def _admin_payload(**over) -> dict:
    base = {
        "user_id": "staff-1",
        "role": "admin",
        "email": "a@spinr.ca",
        "aud": "spinr:admin",
        "token_version": 0,
    }
    base.update(over)
    return base


async def test_verify_admin_payload_inactive_staff_rejected():
    with patch(
        "dependencies.db_supabase.get_rows",
        AsyncMock(return_value=[{"id": "staff-1", "is_active": False, "token_version": 0}]),
    ):
        from dependencies import _verify_admin_payload

        with pytest.raises(HTTPException) as exc:
            await _verify_admin_payload(_admin_payload())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_ACCOUNT_INACTIVE"


async def test_verify_admin_payload_missing_staff_row_rejected():
    with patch("dependencies.db_supabase.get_rows", AsyncMock(return_value=[])):
        from dependencies import _verify_admin_payload

        with pytest.raises(HTTPException) as exc:
            await _verify_admin_payload(_admin_payload())
    assert exc.value.detail == "ERR_ACCOUNT_INACTIVE"


async def test_verify_admin_payload_stale_token_version_rejected():
    with patch(
        "dependencies.db_supabase.get_rows",
        AsyncMock(return_value=[{"id": "staff-1", "is_active": True, "token_version": 5}]),
    ):
        from dependencies import _verify_admin_payload

        with pytest.raises(HTTPException) as exc:
            await _verify_admin_payload(_admin_payload(token_version=2))
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_SESSION_REVOKED"


async def test_verify_admin_payload_idle_timeout_rejected():
    stale_activity = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with patch(
        "dependencies.db_supabase.get_rows",
        AsyncMock(
            return_value=[
                {
                    "id": "staff-1",
                    "is_active": True,
                    "token_version": 0,
                    "last_activity_at": stale_activity,
                }
            ]
        ),
    ):
        from dependencies import _verify_admin_payload

        with pytest.raises(HTTPException) as exc:
            await _verify_admin_payload(_admin_payload())
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_IDLE_TIMEOUT"


async def test_verify_admin_payload_malformed_last_activity_lets_through():
    with (
        patch(
            "dependencies.db_supabase.get_rows",
            AsyncMock(
                return_value=[
                    {
                        "id": "staff-1",
                        "is_active": True,
                        "token_version": 0,
                        "last_activity_at": "not-a-timestamp",
                    }
                ]
            ),
        ),
        patch("dependencies.db_supabase.update_one", AsyncMock(return_value=True)),
    ):
        from dependencies import _verify_admin_payload

        result = await _verify_admin_payload(_admin_payload())
    assert result["_admin_verified"] is True


async def test_verify_admin_payload_activity_stamp_failure_lets_through():
    with (
        patch(
            "dependencies.db_supabase.get_rows",
            AsyncMock(return_value=[{"id": "staff-1", "is_active": True, "token_version": 0}]),
        ),
        patch(
            "dependencies.db_supabase.update_one",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        from dependencies import _verify_admin_payload

        # Must not raise — the activity-stamp write is best-effort.
        result = await _verify_admin_payload(_admin_payload())
    assert result["_admin_verified"] is True


async def test_verify_admin_payload_non_admin_payload_returns_none():
    with patch("dependencies.db_supabase.get_rows", AsyncMock()):
        from dependencies import _verify_admin_payload

        result = await _verify_admin_payload({"user_id": "u1", "aud": "spinr:rider"})
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# get_current_user (JWT path) — DB-error propagation, never silently swallowed
# ─────────────────────────────────────────────────────────────────────────────


def _mint_jwt(user_id="u1", **over):
    import jwt as _pyjwt

    from dependencies import JWT_ALGORITHM, JWT_AUD_MOBILE

    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "phone": "+1",
        "aud": JWT_AUD_MOBILE,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "token_version": 0,
    }
    payload.update(over)
    with patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"):
        return _pyjwt.encode(payload, "test-secret-32-chars-minimum!!!!", algorithm=JWT_ALGORITHM)


async def test_jwt_path_user_lookup_database_error_propagates():
    from utils.error_handling import DatabaseError

    token = _mint_jwt()
    with (
        patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"),
        patch("dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
        patch(
            "dependencies.db_supabase.get_user_by_id",
            AsyncMock(side_effect=DatabaseError(details={"original": "conn refused"})),
        ),
    ):
        from dependencies import get_current_user

        with pytest.raises(DatabaseError):
            await get_current_user(_creds(token))


async def test_jwt_path_user_lookup_unexpected_error_wrapped_as_database_error():
    from utils.error_handling import DatabaseError

    token = _mint_jwt()
    with (
        patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"),
        patch("dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
        patch(
            "dependencies.db_supabase.get_user_by_id",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        from dependencies import get_current_user

        with pytest.raises(DatabaseError):
            await get_current_user(_creds(token))


async def test_jwt_path_driver_lookup_database_error_propagates():
    from utils.error_handling import DatabaseError

    token = _mint_jwt()
    user_row = {"id": "u1", "phone": "+1", "token_version": 0}
    with (
        patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"),
        patch("dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user_row))),
        patch(
            "dependencies.db_supabase.get_driver_by_user_id_cached",
            AsyncMock(side_effect=DatabaseError(details={"original": "conn refused"})),
        ),
    ):
        from dependencies import get_current_user

        with pytest.raises(DatabaseError):
            await get_current_user(_creds(token))


# ─────────────────────────────────────────────────────────────────────────────
# get_current_user_allow_expired — remaining branches
# ─────────────────────────────────────────────────────────────────────────────


async def test_allow_expired_admin_audience_gets_no_grace():
    """The SOS grace window is mobile-only — an admin-audience token must not
    be granted grace even if expired."""
    import jwt as _pyjwt

    from dependencies import JWT_ALGORITHM

    now = datetime.now(timezone.utc)
    expired_admin_payload = {
        "user_id": "staff-1",
        "aud": "spinr:admin",
        "exp": now - timedelta(hours=1),
        "role": "admin",
        "email": "a@spinr.ca",
    }
    with patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"):
        token = _pyjwt.encode(expired_admin_payload, "test-secret-32-chars-minimum!!!!", algorithm=JWT_ALGORITHM)

    with (
        patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"),
        patch("dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
    ):
        from dependencies import get_current_user_allow_expired

        with pytest.raises(HTTPException) as exc:
            await get_current_user_allow_expired(_creds(token))
    assert exc.value.status_code == 401


async def test_allow_expired_not_actually_expired_reraises_original():
    """A 401 for a reason OTHER than expiry (e.g. session revocation) must
    not be overridden by the grace path — only genuine expiry gets grace.
    Mint a NOT-expired token whose claimed token_version is stale relative
    to the stored row, so get_current_user's inner 401 is ERR_SESSION_REVOKED,
    not expiry — the grace path must re-raise that original 401 untouched."""
    token = _mint_jwt(token_version=0)
    user_row = {"id": "u1", "phone": "+1", "token_version": 5}  # claim(0) < stored(5)
    with (
        patch("dependencies.settings.JWT_SECRET", "test-secret-32-chars-minimum!!!!"),
        patch("dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
        patch("dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user_row))),
    ):
        from dependencies import get_current_user_allow_expired

        with pytest.raises(HTTPException) as exc:
            await get_current_user_allow_expired(_creds(token))
    assert exc.value.status_code == 401
    assert exc.value.detail == "ERR_SESSION_REVOKED"
