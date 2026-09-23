"""
P1-11: Token refresh mid-trip (E11) — backend endpoint

The backend refresh endpoint (POST /auth/refresh) issues a new access
token + rotated refresh token. These tests pin:
  - Valid refresh token → new tokens returned
  - Invalid / revoked token → 401 (no oracle leakage)
  - Admin tokens rejected (audience guard)
  - Rotated token replaces the old one (replay-attack prevention)

Client-side retry behavior is tested separately in
shared/api/__tests__/client.refresh.test.ts.

Run:
    pytest backend/tests/test_p1_token_refresh.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request as StarletteRequest


def _make_request(user_agent: str = "", refresh_token: str = "") -> StarletteRequest:
    """Return a real Starlette Request so SlowAPI's rate-limit decorator accepts it."""
    headers = []
    if user_agent:
        headers.append((b"user-agent", user_agent.encode()))
    if refresh_token:
        headers.append((b"cookie", f"refresh_token={refresh_token}".encode()))
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/refresh",
            "query_string": b"",
            "headers": headers,
        }
    )


USER_ID = "user_p1_11"
OLD_REFRESH_ROW_ID = "rtk-row-001"


def _refresh_row(audience: str = "rider", **extra) -> dict:
    return {
        "id": OLD_REFRESH_ROW_ID,
        "user_id": USER_ID,
        "audience": audience,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _user_row() -> dict:
    return {
        "id": USER_ID,
        "phone": "+15551234567",
        "role": "rider",
        "profile_complete": True,
        "token_version": 0,
        "current_session_id": "sess-abc",
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/refresh — token rotation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRefreshAccessToken:
    """Pins the refresh endpoint's happy-path and failure modes.

    Code under test: backend/routes/auth.py::refresh_access_token (~line 512).
    """

    async def test_valid_rider_refresh_token_returns_new_tokens(self):
        # Import via package attribute so auth_mod matches the live module object
        # regardless of any sys.modules divergence caused by earlier test modules.
        from backend.routes import auth as auth_mod

        new_raw_token = "new-refresh-raw-xyz"
        refresh_expires = datetime.now(timezone.utc) + timedelta(days=30)

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=_user_row())),
            patch.object(
                auth_mod,
                "issue_refresh_token",
                AsyncMock(return_value=(new_raw_token, "hashed", refresh_expires)),
            ),
            patch.object(auth_mod, "create_jwt_token", return_value="new-access-token-abc"),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-refresh-raw"

            result = await auth_mod.refresh_access_token(
                request=_make_request(user_agent="TestApp/1.0", refresh_token="old-refresh-raw"),
                response=MagicMock(),
                body=_Body(),
            )

        # Tokens are returned in BOTH the JSON body AND HTTP-only cookies:
        # web clients use the cookies; React Native clients read the body
        # because RN's fetch has no browser cookie jar (see refresh_access_token).
        assert result.token == "new-access-token-abc"
        assert result.refresh_token == new_raw_token
        assert result.access_expires_at is not None

    async def test_response_carries_expires_in_matching_access_ttl(self):
        """Sentry CRIMSON-SMOKE-7445-10F/10Y/SE: driver-app's backgroundAuth.ts
        and shared/store/authStore.ts both destructured a response.expires_in
        that RefreshResponse never actually sent, so every background token
        refresh (and, more subtly, every foreground one) treated a successful
        rotation as a failure. RefreshResponse now carries expires_in as a
        belt-and-suspenders duplicate of access_expires_at, matching
        AuthResponse's existing field -- pin that it's present and correct so
        this can't silently regress again."""
        from backend.core.config import settings
        from backend.routes import auth as auth_mod

        refresh_expires = datetime.now(timezone.utc) + timedelta(days=30)

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=_user_row())),
            patch.object(
                auth_mod,
                "issue_refresh_token",
                AsyncMock(return_value=("new-refresh-raw-xyz", "hashed", refresh_expires)),
            ),
            patch.object(auth_mod, "create_jwt_token", return_value="new-access-token-abc"),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-refresh-raw"

            result = await auth_mod.refresh_access_token(
                request=_make_request(user_agent="TestApp/1.0", refresh_token="old-refresh-raw"),
                response=MagicMock(),
                body=_Body(),
            )

        assert result.expires_in == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    async def test_invalid_refresh_token_returns_401(self):
        """Revoked / unknown refresh tokens must return 401 without distinguishing
        between the failure modes (no oracle)."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod
        from backend.utils.error_handling import SpinrException

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=None)),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "bad-or-revoked-token"

            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

        assert exc_info.value.status_code == 401

    async def test_admin_audience_refresh_token_rejected(self):
        """Admin tokens must not be exchanged via the rider refresh endpoint —
        privilege escalation guard."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod
        from backend.utils.error_handling import SpinrException

        with (
            patch.object(
                auth_mod,
                "lookup_refresh_token",
                AsyncMock(return_value=_refresh_row(audience="admin")),
            ),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "admin-refresh-token"

            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

        assert exc_info.value.status_code == 401

    async def test_user_not_in_db_returns_401(self):
        """If the user referenced by the refresh token no longer exists, 401."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod
        from backend.utils.error_handling import SpinrException

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=None)),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "valid-token-deleted-user"

            with pytest.raises((HTTPException, SpinrException)) as exc_info:
                await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

        assert exc_info.value.status_code == 401

    async def test_access_expires_at_uses_minutes_ttl_not_legacy_days(self):
        """Regression: /auth/refresh reported access_expires_at 30 days out
        (legacy ACCESS_TOKEN_TTL_DAYS) while the JWT itself expired in
        ACCESS_TOKEN_EXPIRE_MINUTES. Clients trusted the field and never
        scheduled a proactive refresh, so every session degraded to
        reactive 401-retry. The field must match the real token TTL."""
        from backend.core.config import settings
        from backend.routes import auth as auth_mod

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=_user_row())),
            patch.object(
                auth_mod,
                "issue_refresh_token",
                AsyncMock(return_value=("new-raw", "hashed", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
            patch.object(auth_mod, "create_jwt_token", return_value="access-tok"),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-raw"

            before = datetime.now(timezone.utc)
            result = await auth_mod.refresh_access_token(
                request=_make_request(user_agent="UA", refresh_token="old-raw"), response=MagicMock(), body=_Body()
            )
            after = datetime.now(timezone.utc)

        expected_low = before + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        expected_high = after + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        assert expected_low <= result.access_expires_at <= expected_high, (
            f"access_expires_at {result.access_expires_at} must equal now + "
            f"{settings.ACCESS_TOKEN_EXPIRE_MINUTES} min (the real JWT exp), not a legacy days TTL"
        )

    async def test_new_token_is_minted_with_replaces_reference(self):
        """The new refresh token must reference the old row (replaces=) so the
        old token is revoked on rotation and replay attacks are blocked."""
        from backend.routes import auth as auth_mod

        issue_calls = []

        async def _capture_issue(user_id, audience, user_agent, ip, replaces, token_version):
            issue_calls.append({"replaces": replaces})
            return ("new-raw", "hashed", datetime.now(timezone.utc) + timedelta(days=30))

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=_user_row())),
            patch.object(auth_mod, "issue_refresh_token", AsyncMock(side_effect=_capture_issue)),
            patch.object(auth_mod, "create_jwt_token", return_value="access-tok"),
            patch.object(auth_mod, "get_real_client_ip", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-raw"

            await auth_mod.refresh_access_token(
                request=_make_request(user_agent="UA", refresh_token="old-raw"), response=MagicMock(), body=_Body()
            )

        assert issue_calls, "issue_refresh_token was not called"
        assert issue_calls[0]["replaces"] == OLD_REFRESH_ROW_ID, (
            "New token must reference the old row id so the old token is revoked on rotation"
        )


@pytest.mark.anyio
@pytest.mark.parametrize("versions", [(3,), (2, 3), (2, 2)])
async def test_refresh_preserves_parent_generation_across_login_race(versions):
    from backend.routes import auth
    from backend.utils.error_handling import TokenExpiredException

    row = {"id": "old-row", "user_id": "user", "audience": "driver", "token_version": 2}
    users = [{"id": "user", "token_version": version, "current_session_id": "session"} for version in versions]
    with (
        patch.object(auth, "lookup_refresh_token", AsyncMock(return_value=row)),
        patch.object(auth.db, "find_one", AsyncMock(side_effect=users)),
        patch.object(
            auth,
            "issue_refresh_token",
            AsyncMock(return_value=("new-refresh", "new-row", datetime.now(timezone.utc) + timedelta(days=30))),
        ) as issue,
        patch.object(auth, "create_jwt_token", MagicMock(return_value="access")) as mint,
    ):
        if versions[-1] == 3:
            with pytest.raises(TokenExpiredException):
                await auth.refresh_access_token(_make_request(), MagicMock(), auth.RefreshRequest(refresh_token="old"))
            mint.assert_not_called()
            if len(versions) == 1:
                issue.assert_not_awaited()
        else:
            result = await auth.refresh_access_token(
                _make_request(), MagicMock(), auth.RefreshRequest(refresh_token="old")
            )
            assert result.token == "access"
            assert mint.call_args.kwargs["token_version"] == 2
        if issue.await_count:
            assert issue.call_args.kwargs["token_version"] == 2
