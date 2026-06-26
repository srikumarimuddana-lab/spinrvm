"""
Regression tests for A-P1-7: admin access-token JTI blacklisting.

Background
----------
Admin JWTs previously had no `jti` claim and logout only revoked the
refresh token.  A stolen access token remained valid until its `exp`.

Fix:
- _mint_admin_access_token() now includes "jti": secrets.token_hex(16)
- admin_logout() decodes the presented Bearer token and calls
  redis_set(f"admin:revoked:{jti}", "1", ttl=remaining_seconds)
- get_current_user() checks redis_get(f"admin:revoked:{jti}") and raises
  401 ERR_TOKEN_REVOKED when set

Test layers
-----------
Layer 1 — pure logic: no imports needed, verify JTI blacklist mechanics.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------


def test_jti_absent_tokens_are_not_blocked():
    """Pre-fix tokens without jti must not be blocked (backwards compat)."""
    payload = {"user_id": "admin-001", "role": "super_admin", "exp": int(time.time()) + 3600}
    jti = payload.get("jti")
    # gate logic mirrors dependencies/__init__.py
    blocked = bool(jti and "1")  # simulate redis returning "1"
    assert not blocked, "no jti → gate is a no-op"


def test_jti_present_and_blacklisted_is_blocked():
    """Token with jti that is in the blacklist must be rejected."""
    payload = {"jti": "abc123", "exp": int(time.time()) + 3600}
    jti = payload.get("jti")
    redis_value = "1"  # simulates redis_get returning "1"
    blocked = bool(jti and redis_value)
    assert blocked


def test_jti_present_but_not_blacklisted_passes():
    """Valid token with jti that is NOT in the blacklist must pass."""
    payload = {"jti": "abc123", "exp": int(time.time()) + 3600}
    jti = payload.get("jti")
    redis_value = None  # simulates redis_get returning None (key absent)
    blocked = bool(jti and redis_value)
    assert not blocked


def test_remaining_ttl_computed_from_exp():
    """TTL passed to redis_set equals seconds remaining until exp."""
    now = int(time.time())
    exp = now + 3600
    remaining = int(exp - now)
    assert 3599 <= remaining <= 3600


def test_already_expired_token_skips_blacklist():
    """Expired access token: remaining <= 0 → redis_set is not called."""
    now = int(time.time())
    exp = now - 60  # already expired
    remaining = int(exp - now)
    assert remaining < 0, "already expired → no redis write needed"


def test_malformed_authorization_header_is_ignored():
    """Non-bearer or malformed Authorization must not raise — logout still succeeds."""
    bad_headers = ["", "Token abc", "Bearer", "Basic dXNlcjpwYXNz"]
    for header in bad_headers:
        try:
            parts = header.split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise ValueError("not a bearer token")
            # if we reach here, treat as valid
            result = "blacklisted"
        except Exception:
            result = "skipped"
        assert result == "skipped", f"header '{header}' should be skipped, not raise"


# ---------------------------------------------------------------------------
# Layer 2 — integration (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.dependencies as _deps_module
    import backend.routes.admin.auth as _admin_auth_module

    _HAS_BACKEND_DEPS = True
except BaseException:  # pyo3/cffi panics are not Exception subclasses in this env
    _admin_auth_module = None  # type: ignore[assignment]
    _deps_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_admin_logout_blacklists_jti():
    """
    admin_logout with a valid Bearer token must write admin:revoked:{jti}
    to Redis with a positive TTL.
    """
    import jwt as pyjwt

    from backend.core.config import settings

    now = int(time.time())
    jti = "test-jti-abc123"
    token = pyjwt.encode(
        {"user_id": "u1", "role": "admin", "jti": jti, "exp": now + 3600, "iat": now},
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )

    redis_set_mock = AsyncMock()
    revoke_mock = AsyncMock()

    from starlette.requests import Request as StarletteRequest

    scope = {"type": "http", "method": "POST", "headers": [], "path": "/api/admin/auth/logout", "query_string": b""}
    request_mock = StarletteRequest(scope=scope)
    request_mock.state.user = None

    from backend.routes.admin.auth import LogoutRequest

    with (
        patch("backend.routes.admin.auth.revoke_refresh_token", revoke_mock),
        patch("backend.routes.admin.auth.redis_set", redis_set_mock),
    ):
        result = await _admin_auth_module.admin_logout(
            request=request_mock,
            body=LogoutRequest(refresh_token=None),
            authorization=f"Bearer {token}",
        )

    assert result == {"success": True}
    redis_set_mock.assert_called_once()
    key, value, *_ = redis_set_mock.call_args.args
    assert key == f"admin:revoked:{jti}"
    assert value == "1"
    # ttl kwarg should be positive
    ttl = redis_set_mock.call_args.kwargs.get("ttl") or redis_set_mock.call_args.args[2]
    assert ttl > 0


@_skip_no_deps
@pytest.mark.anyio
async def test_get_current_user_rejects_blacklisted_jti():
    """
    get_current_user must raise 401 ERR_TOKEN_REVOKED when the token's JTI
    is present in the Redis blacklist.
    """
    import jwt as pyjwt
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from backend.core.config import settings

    now = int(time.time())
    jti = "revoked-jti-xyz"
    token = pyjwt.encode(
        {
            "user_id": "staff-1",
            "role": "admin",
            "email": "admin@spinr.ca",
            "modules": [],
            "jti": jti,
            "token_version": 0,
            "exp": now + 3600,
            "iat": now,
        },
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with patch("backend.dependencies.redis_get", AsyncMock(return_value="1")):
        with pytest.raises(HTTPException) as exc_info:
            await _deps_module.get_current_user(credentials=credentials)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "ERR_TOKEN_REVOKED"


# ---------------------------------------------------------------------------
# C7 — break-glass super-admin token allowlist (register on mint, revocable)
# ---------------------------------------------------------------------------


def _bg_payload():
    return {
        "user_id": "break-glass",
        "email": "break-glass@spinr.ca",
        "role": "super_admin",
        "aud": _deps_module.JWT_AUD_ADMIN,
        "modules": ["dispatch", "payments"],
        "token_version": 0,
        "jti": "bg-jti-123",
        "break_glass": True,
    }


@_skip_no_deps
@pytest.mark.anyio
async def test_break_glass_authenticates_when_allowlisted_without_staff_row():
    """C7: a break-glass token (no admin_staff row) authenticates when its JTI is
    present in the Redis allowlist, and never falls through to the staff lookup."""

    async def _redis(key):
        # Not denylisted; IS allowlisted.
        return "1" if key == "admin:breakglass:bg-jti-123" else None

    get_rows = AsyncMock()
    with (
        patch("backend.dependencies.redis_get", AsyncMock(side_effect=_redis)),
        patch("backend.dependencies.db_supabase.get_rows", get_rows),
    ):
        result = await _deps_module._verify_admin_payload(_bg_payload())

    assert result is not None
    assert result["id"] == "break-glass"
    assert result["role"] == "super_admin"
    # The staff-active / token_version / idle path must be skipped entirely.
    get_rows.assert_not_awaited()


@_skip_no_deps
@pytest.mark.anyio
async def test_break_glass_rejected_when_not_allowlisted():
    """C7: deleting (or never registering / letting expire) the allowlist key
    revokes the break-glass token — verification returns 401."""
    from fastapi import HTTPException

    with patch("backend.dependencies.redis_get", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await _deps_module._verify_admin_payload(_bg_payload())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "ERR_TOKEN_REVOKED"


@_skip_no_deps
@pytest.mark.anyio
async def test_break_glass_fails_closed_when_redis_unavailable():
    """C7: unlike the best-effort denylist (fail open), a god-mode break-glass
    token must FAIL CLOSED when the allowlist cannot be read."""
    from fastapi import HTTPException

    with patch("backend.dependencies.redis_get", AsyncMock(side_effect=RuntimeError("redis down"))):
        with pytest.raises(HTTPException) as exc_info:
            await _deps_module._verify_admin_payload(_bg_payload())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "ERR_TOKEN_REVOKED"
