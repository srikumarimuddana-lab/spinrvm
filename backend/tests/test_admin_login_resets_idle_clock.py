"""admin_login must reset last_activity_at so idle-timeout is recoverable.

Regression (incident): a staff member who idled past the 30-minute admin
idle-timeout got stuck in a login → 401 → refresh → 401 → logout loop while
other staff worked fine. _verify_admin_payload raises ERR_IDLE_TIMEOUT when
last_activity_at is stale and only refreshes the column *after* that raise, so
the timestamp never advanced — and neither login nor refresh reset it. A fresh
password authentication must clear the idle clock; this asserts admin_login
writes a current last_activity_at for every staff session path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

from backend.core.config import settings
from backend.routes.admin import auth as admin_auth

EMAIL = "ops@spinr.ca"
PASSWORD = "x" * 24
STALE = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()

pytestmark = pytest.mark.anyio


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
        "id": "staff-idle",
        "email": EMAIL,
        "password_hash": "bcrypt$stub",
        "role": "operations",
        "modules": ["dashboard"],
        "is_active": True,
        "token_version": 0,
        "last_activity_at": STALE,
        **extra,
    }


class _LoginBody:
    email = EMAIL
    password = PASSWORD


def _patches(staff_row: dict, update_mock: AsyncMock):
    return (
        patch.object(admin_auth, "_is_account_locked", AsyncMock(return_value=False)),
        patch.object(admin_auth, "_clear_login_failures", AsyncMock()),
        patch.object(admin_auth, "_record_login_failure", AsyncMock()),
        patch.object(admin_auth.db_supabase, "get_rows", AsyncMock(return_value=[staff_row])),
        patch.object(admin_auth.db_supabase, "update_one", update_mock),
        patch.object(admin_auth, "verify_password", MagicMock(return_value=(True, False))),
        patch.object(admin_auth, "get_remote_address", MagicMock(return_value="127.0.0.1")),
    )


def _asserts_fresh_activity(update_mock: AsyncMock) -> None:
    # Find the admin_staff update carrying last_activity_at and confirm it is
    # current (well inside the 30-minute window), i.e. the stale value is gone.
    payloads = [c.args[2] for c in update_mock.await_args_list if len(c.args) >= 3]
    activity = next((p["last_activity_at"] for p in payloads if "last_activity_at" in p), None)
    assert activity is not None, "login did not write last_activity_at"
    written = datetime.fromisoformat(activity.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - written).total_seconds()
    assert age < 60, f"last_activity_at not reset to now (age {age:.0f}s)"


async def test_direct_login_resets_idle_clock():
    update_mock = AsyncMock()
    p = _patches(_staff_row(), update_mock)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
        with (
            patch.object(settings, "ADMIN_MFA_ENFORCED", False),
            patch.object(
                admin_auth,
                "issue_refresh_token",
                AsyncMock(return_value=("refresh-raw", "hashed", datetime.now(timezone.utc))),
            ),
        ):
            result = await admin_auth.admin_login(request=_make_request(), response=MagicMock(), body=_LoginBody())
    assert "token" in result
    _asserts_fresh_activity(update_mock)


async def test_mfa_enrollment_login_still_resets_idle_clock():
    # Even when the password step only buys an enrollment token (no session
    # yet), the idle clock must already be reset so the post-enrollment session
    # is not immediately idle-locked.
    update_mock = AsyncMock()
    p = _patches(_staff_row(), update_mock)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
        with patch.object(settings, "ADMIN_MFA_ENFORCED", True):
            result = await admin_auth.admin_login(request=_make_request(), response=MagicMock(), body=_LoginBody())
    assert result.get("mfa_enrollment_required") is True
    _asserts_fresh_activity(update_mock)
