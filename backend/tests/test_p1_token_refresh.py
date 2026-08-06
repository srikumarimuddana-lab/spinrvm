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
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
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

    async def test_invalid_refresh_token_returns_401(self):
        """Revoked / unknown refresh tokens must return 401 without distinguishing
        between the failure modes (no oracle)."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod
        from backend.utils.error_handling import SpinrException

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=None)),
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
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
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
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
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
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
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
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

    async def test_response_includes_expires_in_seconds(self):
        """Regression: the mobile clients read ``expires_in``, not ``access_expires_at``.

        ``authStore.refreshTokens()`` destructures ``expires_in`` from this
        response and ``setTokens()`` computes ``Date.now() + expires_in * 1000``.
        The field was absent here (only on the login ``AuthResponse``), so the
        arithmetic produced ``NaN`` on every refresh. ``tokenExpiresAt`` then
        read as falsy, ``ensureFreshToken()`` bailed on its
        ``!tokenExpiresAt`` guard, and the proactive 2-minute refresh was dead
        for the rest of the session — every expiry became a reactive 401 burst.
        The driver app also persists the value as ``token_expires_at``, which
        its headless location task parses to authorise batch uploads, so the
        ``"NaN"`` string silently disabled those too.

        Fixing ``access_expires_at`` (see the test above) did not help: no
        mobile client reads that field. This pins the one they do.
        """
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
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-raw"

            result = await auth_mod.refresh_access_token(
                request=_make_request(user_agent="UA", refresh_token="old-raw"), response=MagicMock(), body=_Body()
            )

        expected = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        assert result.expires_in == expected, (
            f"expires_in must be the access-token TTL in seconds ({expected}); "
            "the mobile clients compute tokenExpiresAt from this field and get NaN without it"
        )
        # Must agree with access_expires_at — two spellings of one fact must not drift.
        skew = abs((result.access_expires_at - datetime.now(timezone.utc)).total_seconds() - result.expires_in)
        assert skew < 5, (
            f"expires_in ({result.expires_in}s) and access_expires_at "
            f"({result.access_expires_at}) disagree by {skew:.1f}s"
        )

    async def test_new_token_is_minted_with_replaces_reference(self):
        """The new refresh token must reference the old row (replaces=) so the
        old token is revoked on rotation and replay attacks are blocked."""
        from backend.routes import auth as auth_mod

        issue_calls = []

        async def _capture_issue(user_id, audience, user_agent, ip, replaces):
            issue_calls.append({"replaces": replaces})
            return ("new-raw", "hashed", datetime.now(timezone.utc) + timedelta(days=30))

        with (
            patch.object(auth_mod, "lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch.object(auth_mod.db, "find_one", AsyncMock(return_value=_user_row())),
            patch.object(auth_mod, "issue_refresh_token", AsyncMock(side_effect=_capture_issue)),
            patch.object(auth_mod, "create_jwt_token", return_value="access-tok"),
            patch.object(auth_mod, "get_remote_address", return_value="127.0.0.1"),
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
