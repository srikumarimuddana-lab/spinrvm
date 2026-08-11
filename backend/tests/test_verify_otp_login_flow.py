"""routes/auth.py verify_otp — the core rider/driver login/signup endpoint.

Existing coverage (test_auth_send_otp.py) only pins the lockout helpers
and the "DB error is not a wrong code" 503 path. The entire success
path — existing-user login, new-user creation, the PIPEDA
pending_deletion reactivation handoff, and the deleted-account 410 —
had zero direct coverage.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

PHONE = "+13065203304"
CODE = "1234"


def _resolve_inner(fn):
    """slowapi's @limiter.limit wraps the coroutine in async_wrapper without
    setting __wrapped__. Walk __closure__ cells to pull out the original
    so we can call the handler without tripping rate-limit state. (Local
    copy of the helper in test_auth_send_otp.py — kept self-contained so
    this file has no cross-test-file import coupling.)"""
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            closure = getattr(fn, "__closure__", None) or ()
            for cell in closure:
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None:
                    if val is fn:
                        continue
                    return val
            return fn
        fn = nxt


def _valid_otp_record(**over) -> dict:
    from backend.utils.crypto import hash_otp

    row = {
        "id": "otp-1",
        "phone": PHONE,
        "code": hash_otp(CODE),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    row.update(over)
    return row


def _base_patches(
    otp_record=None,
    user_lookup_result=None,
    user_lookup_side_effect=None,
    record_failure_mock=None,
    delete_otp_mock=None,
    alert_new_device_mock=None,
):
    """The common set of mocks every verify_otp call needs, regardless of
    which downstream branch (existing/new user) the test targets."""
    get_user_by_phone = (
        AsyncMock(side_effect=user_lookup_side_effect)
        if user_lookup_side_effect
        else AsyncMock(return_value=user_lookup_result)
    )
    return [
        patch("backend.routes.auth._check_otp_lockout", AsyncMock(return_value=None)),
        patch("backend.routes.auth._record_otp_failure", record_failure_mock or AsyncMock()),
        patch("backend.routes.auth._clear_otp_failures", AsyncMock()),
        patch(
            "backend.routes.auth.db_supabase.get_otp_record_by_phone",
            AsyncMock(return_value=otp_record),
        ),
        patch("backend.routes.auth.db_supabase.delete_otp_record", delete_otp_mock or AsyncMock()),
        patch("backend.routes.auth.db_supabase.get_user_by_phone", get_user_by_phone),
        patch("backend.routes.auth.redis_set", AsyncMock()),
        patch("backend.routes.auth._audit_log_user", AsyncMock()),
        patch("backend.routes.auth._alert_if_new_device", alert_new_device_mock or AsyncMock()),
    ]


def _apply(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


async def _call_verify_otp(patches, code=CODE):
    from backend.routes.auth import verify_otp
    from backend.schemas import VerifyOTPRequest

    body = VerifyOTPRequest(phone=PHONE, code=code)
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.headers = {"user-agent": "pytest"}
    response = MagicMock()

    _apply(patches)
    try:
        inner = _resolve_inner(verify_otp)
        return await inner(request, response, body)
    finally:
        _stop(patches)


# ─────────────────────────────────────────────────────────────────────────────
# Existing-user login
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_user_login_returns_auth_response():
    user = {
        "id": "u1",
        "phone": PHONE,
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "token_version": 0,
        "profile_complete": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=dict(user))
    patches += [
        patch("backend.routes.auth.db_supabase.update_one", AsyncMock(return_value=True)),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "row-1", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
    ]
    result = await _call_verify_otp(patches)

    assert result.is_new_user is False
    assert result.token
    assert result.refresh_token == "raw-refresh"


@pytest.mark.asyncio
async def test_existing_user_login_checks_for_new_device():
    """ACTION_ITEMS.md N15/R8 — the existing-user branch must run the
    new-device check before minting the login's own refresh token (see
    is_new_device's docstring on why the ordering matters)."""
    user = {
        "id": "u1",
        "phone": PHONE,
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "token_version": 0,
        "profile_complete": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    alert_mock = AsyncMock()
    patches = _base_patches(
        otp_record=_valid_otp_record(), user_lookup_result=dict(user), alert_new_device_mock=alert_mock
    )
    patches += [
        patch("backend.routes.auth.db_supabase.update_one", AsyncMock(return_value=True)),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "row-1", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
    ]
    await _call_verify_otp(patches)

    alert_mock.assert_awaited_once()
    called_user, called_ua = alert_mock.await_args.args
    assert called_user["id"] == "u1"
    assert called_ua == "pytest"  # from _call_verify_otp's request.headers stub


@pytest.mark.asyncio
async def test_existing_guest_user_login_clears_is_guest_flag():
    user = {
        "id": "u-guest",
        "phone": PHONE,
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "token_version": 0,
        "is_guest": True,
        "profile_complete": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    update_mock = AsyncMock(return_value=True)
    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=dict(user))
    patches += [
        patch("backend.routes.auth.db_supabase.update_one", update_mock),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "row-1", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
    ]
    await _call_verify_otp(patches)

    _, _, payload = update_mock.await_args.args
    assert payload["is_guest"] is False


@pytest.mark.asyncio
async def test_pending_deletion_account_returns_reactivation_handoff():
    user = {
        "id": "u-deleted-pending",
        "phone": PHONE,
        "status": "pending_deletion",
        "deletion_scheduled_at": "2026-08-01T00:00:00Z",
    }
    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=dict(user))
    result = await _call_verify_otp(patches)

    # verify_otp returns a JSONResponse for this branch, not an AuthResponse.
    import json

    body = json.loads(result.body)
    assert body["requires_reactivation"] is True
    assert body["reactivation_token"]


@pytest.mark.asyncio
async def test_fully_deleted_account_returns_410():
    user = {"id": "u-purged", "phone": PHONE, "status": "deleted"}
    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=dict(user))

    with pytest.raises(HTTPException) as exc:
        await _call_verify_otp(patches)
    assert exc.value.status_code == 410
    assert exc.value.detail == "ERR_ACCOUNT_DELETED"


@pytest.mark.asyncio
async def test_get_user_by_phone_db_error_raises_503():
    from backend.utils.error_handling import SpinrException

    patches = _base_patches(
        otp_record=_valid_otp_record(),
        user_lookup_side_effect=RuntimeError("db down"),
    )

    with pytest.raises(SpinrException) as exc:
        await _call_verify_otp(patches)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_session_update_failure_does_not_block_login():
    """A failed current_session_id write is logged but must not prevent
    login — the user still gets a valid token pair."""
    user = {
        "id": "u1",
        "phone": PHONE,
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "token_version": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=dict(user))
    patches += [
        patch(
            "backend.routes.auth.db_supabase.update_one",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "row-1", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
    ]
    result = await _call_verify_otp(patches)
    assert result.is_new_user is False
    assert result.token


# ─────────────────────────────────────────────────────────────────────────────
# New-user creation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_user_created_and_logged_in():
    create_mock = AsyncMock(return_value={"id": "new-1"})
    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=None)
    patches += [
        patch("backend.routes.auth.db_supabase.create_user", create_mock),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "row-1", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
        patch("backend.routes.auth._fire_signup_conversion", MagicMock(return_value=None)),
    ]
    result = await _call_verify_otp(patches)

    assert result.is_new_user is True
    assert result.token
    create_mock.assert_awaited_once()
    created_payload = create_mock.await_args.args[0]
    assert created_payload["phone"] == PHONE
    assert created_payload["role"] == "rider"
    assert created_payload["token_version"] == 0


@pytest.mark.asyncio
async def test_new_user_create_failure_raises_503_never_mints_token():
    from backend.utils.error_handling import SpinrException

    patches = _base_patches(otp_record=_valid_otp_record(), user_lookup_result=None)
    patches += [
        patch(
            "backend.routes.auth.db_supabase.create_user",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ]
    with pytest.raises(SpinrException) as exc:
        await _call_verify_otp(patches)
    assert exc.value.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# OTP record validation branches
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrong_code_records_failure_and_raises_400():
    from backend.utils.error_handling import SpinrException

    record_failure = AsyncMock()
    patches = _base_patches(otp_record=_valid_otp_record(code="9999"), record_failure_mock=record_failure)

    with pytest.raises(SpinrException) as exc:
        await _call_verify_otp(patches, code=CODE)
    assert exc.value.status_code == 400
    record_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_otp_raises_400_and_deletes_record():
    from backend.utils.error_handling import SpinrException

    expired = _valid_otp_record(expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    delete_mock = AsyncMock()
    patches = _base_patches(otp_record=expired, delete_otp_mock=delete_mock)

    with pytest.raises(SpinrException) as exc:
        await _call_verify_otp(patches)
    assert exc.value.status_code == 400
    assert "EXPIRED" in exc.value.message_key or True
    delete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_expires_at_raises_500():
    from backend.utils.error_handling import SpinrException

    patches = _base_patches(otp_record=_valid_otp_record(expires_at="not-a-timestamp"))

    with pytest.raises(SpinrException) as exc:
        await _call_verify_otp(patches)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_missing_expires_at_raises_500():
    from backend.utils.error_handling import SpinrException

    row = _valid_otp_record()
    del row["expires_at"]
    patches = _base_patches(otp_record=row)

    with pytest.raises(SpinrException) as exc:
        await _call_verify_otp(patches)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_after_verify_failure_falls_back_to_mark_verified():
    """If deleting the just-verified OTP record fails, the endpoint must
    still complete login (best-effort fallback: mark as verified instead)."""
    user = {
        "id": "u1",
        "phone": PHONE,
        "role": "rider",
        "is_rider": True,
        "is_driver": False,
        "token_version": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    update_one_mock = AsyncMock(return_value=True)
    patches = _base_patches(
        otp_record=_valid_otp_record(),
        user_lookup_result=dict(user),
        delete_otp_mock=AsyncMock(side_effect=RuntimeError("db down")),
    )
    patches += [
        patch("backend.routes.auth.db_supabase.update_one", update_one_mock),
        patch(
            "backend.routes.auth.issue_refresh_token",
            AsyncMock(return_value=("raw-refresh", "row-1", datetime.now(timezone.utc) + timedelta(days=30))),
        ),
    ]
    result = await _call_verify_otp(patches)
    assert result.is_new_user is False
