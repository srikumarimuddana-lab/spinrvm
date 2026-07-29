"""Coverage-closure tests for backend/routes/admin/auth.py.

Targets branches not already exercised by the sibling admin-auth test files
(test_admin_mfa_*.py, test_admin_login_resets_idle_clock.py,
test_admin_logout_revocation.py, test_admin_token_aud_lockdown.py,
test_admin_revocation_failopen.py, test_admin_privilege_escalation.py,
test_admin_staff_mfa_reset.py, test_admin_security.py):

  - /admin/auth/break-glass: every guard branch (feature-gated off,
    justification too short, rate-limit read/incr failures fail closed,
    rate limit exceeded, invalid token, successful mint incl. audit-log
    write failure being swallowed-but-logged).
  - /admin/auth/unlock: role guard, missing email, target not found,
    idempotent not-locked path, Redis read failure, successful unlock.
  - /admin/auth/mfa/status: unauthenticated, malformed scheme, malformed
    token, super-admin (admin-001) short-circuit, staff not found,
    enabled/disabled staff.
  - /admin/auth/mfa/enroll: happy path (secret + otpauth URI minted).
  - /admin/auth/session: malformed "Authorization" header shapes.
  - /admin/auth/refresh: admin-001 (super admin) branch.
  - /admin/auth/logout-all: malformed auth scheme / bad token branches.

Run:
    pytest backend/tests/test_admin_auth_coverage_gap.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

from backend.routes.admin import auth as admin_auth

STAFF_ID = "staff-coverage-gap"


def _make_request(headers: list[tuple[bytes, bytes]] | None = None) -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/auth/x",
            "query_string": b"",
            "headers": headers or [(b"user-agent", b"TestSuite/1.0")],
            "client": ("127.0.0.1", 1234),
        }
    )


def _staff_row(**overrides) -> dict:
    row = {
        "id": STAFF_ID,
        "email": "coverage@spinr.ca",
        "role": "operations",
        "modules": ["dashboard"],
        "is_active": True,
        "token_version": 0,
        "mfa_enabled": False,
        "password_hash": "irrelevant",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# /admin/auth/break-glass
# ---------------------------------------------------------------------------


class _BgBody:
    def __init__(self, token="tok", justification="genuine emergency access needed"):
        self.token = token
        self.justification = justification


@pytest.mark.anyio
async def test_break_glass_disabled_when_hash_not_configured():
    with patch.object(admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", ""):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody())
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_break_glass_rejects_short_justification():
    with patch.object(admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", "somehash"):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody(justification="short"))
    assert exc_info.value.status_code == 400


# break_glass_access does a *local* re-import of the redis helpers
# (`from utils.redis_client import ...`) inside the function body, which
# shadows the module-level names patched everywhere else in this file — so
# the break-glass tests below must patch `utils.redis_client` directly.
import utils.redis_client as _bg_redis_client  # noqa: E402


@pytest.mark.anyio
async def test_break_glass_fails_closed_when_rate_counter_unreadable():
    with (
        patch.object(admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", "somehash"),
        patch.object(_bg_redis_client, "redis_get", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody())
    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_break_glass_rate_limit_exceeded():
    with (
        patch.object(admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", "somehash"),
        patch.object(_bg_redis_client, "redis_get", AsyncMock(return_value="5")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody())
    assert exc_info.value.status_code == 429


@pytest.mark.anyio
async def test_break_glass_fails_closed_when_increment_fails():
    with (
        patch.object(admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", "somehash"),
        patch.object(_bg_redis_client, "redis_get", AsyncMock(return_value="0")),
        patch.object(_bg_redis_client, "redis_incr", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody())
    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_break_glass_rejects_invalid_token():
    with (
        patch.object(admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", admin_auth.hashlib.sha256(b"correct").hexdigest()),
        patch.object(_bg_redis_client, "redis_get", AsyncMock(return_value="0")),
        patch.object(_bg_redis_client, "redis_incr", AsyncMock(return_value=1)),
        patch.object(_bg_redis_client, "redis_expire", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody(token="wrong"))
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_break_glass_success_mints_token_and_swallows_audit_failure():
    """Happy path: token mints even when the audit-log write itself fails —
    the endpoint must not deny an emergency operator because the DB write
    that documents the emergency failed."""
    correct = "correct-token"
    with (
        patch.object(
            admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", admin_auth.hashlib.sha256(correct.encode()).hexdigest()
        ),
        patch.object(_bg_redis_client, "redis_get", AsyncMock(return_value="1")),
        patch.object(_bg_redis_client, "redis_incr", AsyncMock(return_value=2)),
        patch.object(_bg_redis_client, "redis_expire", AsyncMock()),
        patch.object(_bg_redis_client, "redis_set", AsyncMock()),
        patch.object(admin_auth.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        result = await admin_auth.break_glass_access(_make_request(), _BgBody(token=correct))
    assert result["role"] == "super_admin"
    assert "token" in result and "jti" in result
    payload = admin_auth.jwt.decode(
        result["token"],
        admin_auth.settings.JWT_SECRET,
        algorithms=[admin_auth.settings.ALGORITHM],
        audience=admin_auth.JWT_AUD_ADMIN,
    )
    assert payload["user_id"] == "break-glass"
    assert payload["break_glass"] is True


@pytest.mark.anyio
async def test_break_glass_fails_closed_when_allowlist_registration_fails():
    correct = "correct-token-2"
    with (
        patch.object(
            admin_auth.settings, "BREAK_GLASS_TOKEN_HASH", admin_auth.hashlib.sha256(correct.encode()).hexdigest()
        ),
        patch.object(_bg_redis_client, "redis_get", AsyncMock(return_value="0")),
        patch.object(_bg_redis_client, "redis_incr", AsyncMock(return_value=1)),
        patch.object(_bg_redis_client, "redis_expire", AsyncMock()),
        patch.object(_bg_redis_client, "redis_set", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.break_glass_access(_make_request(), _BgBody(token=correct))
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# /admin/auth/unlock
# ---------------------------------------------------------------------------


class _UnlockBody:
    def __init__(self, email="locked@spinr.ca"):
        self.email = email


@pytest.mark.anyio
async def test_unlock_requires_super_admin():
    actor = {"id": "actor-1", "role": "operations"}
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_unlock(_make_request(), _UnlockBody(), actor=actor)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_unlock_rejects_empty_email():
    actor = {"id": "actor-1", "role": "super_admin"}
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_unlock(_make_request(), _UnlockBody(email="   "), actor=actor)
    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_unlock_target_not_found():
    actor = {"id": "actor-1", "role": "super_admin"}
    with patch.object(admin_auth.db_supabase, "get_rows", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_unlock(_make_request(), _UnlockBody(), actor=actor)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_unlock_fails_closed_on_redis_read_error():
    actor = {"id": "actor-1", "role": "super_admin"}
    with (
        patch.object(
            admin_auth.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "t1", "email": "locked@spinr.ca"}])
        ),
        patch.object(admin_auth, "redis_get", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_unlock(_make_request(), _UnlockBody(), actor=actor)
    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_unlock_is_idempotent_when_not_locked():
    actor = {"id": "actor-1", "role": "super_admin"}
    log_mock = AsyncMock()
    with (
        patch.object(
            admin_auth.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "t1", "email": "locked@spinr.ca"}])
        ),
        patch.object(admin_auth, "redis_get", AsyncMock(return_value="1")),  # below _LOGIN_MAX_FAILURES=5
        patch.object(admin_auth, "log_admin_action", log_mock),
    ):
        result = await admin_auth.admin_unlock(_make_request(), _UnlockBody(), actor=actor)
    assert result == {"unlocked": False, "reason": "not_locked"}
    log_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_unlock_clears_lockout_when_locked():
    actor = {"id": "actor-1", "role": "super_admin"}
    clear_mock = AsyncMock()
    log_mock = AsyncMock()
    with (
        patch.object(
            admin_auth.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "t1", "email": "locked@spinr.ca"}])
        ),
        patch.object(admin_auth, "redis_get", AsyncMock(return_value="5")),  # >= _LOGIN_MAX_FAILURES
        patch.object(admin_auth, "_clear_login_failures", clear_mock),
        patch.object(admin_auth, "log_admin_action", log_mock),
    ):
        result = await admin_auth.admin_unlock(_make_request(), _UnlockBody(), actor=actor)
    assert result == {"unlocked": True}
    clear_mock.assert_awaited_once()
    log_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# /admin/auth/mfa/status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mfa_status_requires_authorization():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_mfa_status(authorization=None)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_mfa_status_rejects_non_bearer_scheme():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_mfa_status(authorization="Basic abc123")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_mfa_status_rejects_malformed_token():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_mfa_status(authorization="Bearer not-a-jwt")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


@pytest.mark.anyio
async def test_mfa_status_super_admin_short_circuits():
    token, _ = admin_auth._mint_admin_access_token("admin-001", "a@b.com", "super_admin", [], 0)
    result = await admin_auth.admin_mfa_status(authorization=f"Bearer {token}")
    assert result == {"mfa_enabled": False, "available": False, "enforced": admin_auth.settings.ADMIN_MFA_ENFORCED}


@pytest.mark.anyio
async def test_mfa_status_staff_not_found():
    token, _ = admin_auth._mint_admin_access_token(STAFF_ID, "s@b.com", "operations", ["dashboard"], 0)
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_mfa_status(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_mfa_status_returns_enabled_state_for_staff():
    token, _ = admin_auth._mint_admin_access_token(STAFF_ID, "s@b.com", "operations", ["dashboard"], 0)
    with patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row(mfa_enabled=True))):
        result = await admin_auth.admin_mfa_status(authorization=f"Bearer {token}")
    assert result["mfa_enabled"] is True
    assert result["available"] is True


# ---------------------------------------------------------------------------
# /admin/auth/mfa/enroll
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mfa_enroll_mints_secret_and_uri():
    token, _ = admin_auth._mint_admin_access_token(STAFF_ID, "s@b.com", "operations", ["dashboard"], 0)
    update_mock = AsyncMock()
    with (
        patch.object(admin_auth.db, "find_one", AsyncMock(return_value=_staff_row())),
        patch.object(admin_auth, "redis_get", AsyncMock(return_value=None)),
        patch.object(admin_auth.db_supabase, "update_one", update_mock),
    ):
        result = await admin_auth.admin_mfa_enroll(_make_request(), authorization=f"Bearer {token}")
    assert "secret" in result
    assert result["otpauth_uri"].startswith("otpauth://")
    update_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# /admin/auth/session — malformed Authorization header shapes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_no_authorization_header():
    result = await admin_auth.get_session(authorization=None)
    assert result.authenticated is False


@pytest.mark.anyio
async def test_session_header_missing_token_part():
    """'Bearer' alone fails the str.split()-into-two-parts unpack (ValueError)."""
    result = await admin_auth.get_session(authorization="Bearer")
    assert result.authenticated is False


@pytest.mark.anyio
async def test_session_header_wrong_scheme():
    token, _ = admin_auth._mint_admin_access_token("admin-001", "a@b.com", "super_admin", [], 0)
    result = await admin_auth.get_session(authorization=f"Basic {token}")
    assert result.authenticated is False


@pytest.mark.anyio
async def test_session_valid_token_returns_authenticated_user():
    token, _ = admin_auth._mint_admin_access_token("admin-001", "a@b.com", "super_admin", ["dashboard"], 0)
    result = await admin_auth.get_session(authorization=f"Bearer {token}")
    assert result.authenticated is True
    assert result.user["id"] == "admin-001"


# ---------------------------------------------------------------------------
# /admin/auth/refresh — admin-001 (super admin, env-var creds) branch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_super_admin_branch():
    with (
        patch.object(
            admin_auth,
            "lookup_refresh_token",
            AsyncMock(return_value={"user_id": "admin-001", "audience": "admin", "id": "old-rt"}),
        ),
        patch.object(
            admin_auth,
            "issue_refresh_token",
            AsyncMock(return_value=("new-raw", "hash", admin_auth.datetime.now(admin_auth.timezone.utc))),
        ),
        patch.object(admin_auth, "get_real_client_ip", MagicMock(return_value="127.0.0.1")),
    ):
        result = await admin_auth.admin_refresh(_make_request(), admin_auth.RefreshRequest(refresh_token="rt"))
    assert result["token"]
    payload = admin_auth.jwt.decode(
        result["token"],
        admin_auth.settings.JWT_SECRET,
        algorithms=[admin_auth.settings.ALGORITHM],
        audience=admin_auth.JWT_AUD_ADMIN,
    )
    assert payload["user_id"] == "admin-001"
    assert payload["role"] == "super_admin"


@pytest.mark.anyio
async def test_refresh_rejects_wrong_audience():
    with patch.object(
        admin_auth,
        "lookup_refresh_token",
        AsyncMock(return_value={"user_id": "u1", "audience": "rider", "id": "old-rt"}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth.admin_refresh(_make_request(), admin_auth.RefreshRequest(refresh_token="rt"))
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# /admin/auth/logout-all — malformed token branches
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logout_all_rejects_missing_authorization():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_logout_all(_make_request(), authorization=None)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_logout_all_rejects_malformed_token():
    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_logout_all(_make_request(), authorization="Bearer not-a-jwt")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# _is_totp_locked / _is_account_locked — Redis-unavailable fail-closed paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_is_totp_locked_fails_closed_on_redis_error():
    with patch.object(admin_auth, "redis_get", AsyncMock(side_effect=RuntimeError("down"))):
        with pytest.raises(HTTPException) as exc_info:
            await admin_auth._is_totp_locked(STAFF_ID)
    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_is_totp_locked_false_below_threshold():
    with patch.object(admin_auth, "redis_get", AsyncMock(return_value="2")):
        assert await admin_auth._is_totp_locked(STAFF_ID) is False


@pytest.mark.anyio
async def test_record_totp_failure_logs_but_does_not_raise_on_redis_error():
    with patch.object(admin_auth, "redis_incr", AsyncMock(side_effect=RuntimeError("down"))):
        await admin_auth._record_totp_failure(STAFF_ID)  # must not raise


@pytest.mark.anyio
async def test_clear_totp_failures_logs_but_does_not_raise_on_redis_error():
    with patch.object(admin_auth, "redis_delete", AsyncMock(side_effect=RuntimeError("down"))):
        await admin_auth._clear_totp_failures(STAFF_ID)  # must not raise
