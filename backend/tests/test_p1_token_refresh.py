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


def _make_request(user_agent: str = "") -> StarletteRequest:
    """Return a real Starlette Request so SlowAPI's rate-limit decorator accepts it."""
    headers = []
    if user_agent:
        headers.append((b"user-agent", user_agent.encode()))
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
        "created_at": datetime.utcnow().isoformat(),
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
        from backend.routes import auth as auth_mod

        new_raw_token = "new-refresh-raw-xyz"
        refresh_expires = datetime.now(timezone.utc) + timedelta(days=30)

        with (
            patch("backend.routes.auth.lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch("backend.routes.auth.db.find_one", AsyncMock(return_value=_user_row())),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=(new_raw_token, "hashed", refresh_expires)),
            ),
            patch("backend.routes.auth.create_jwt_token", return_value="new-access-token-abc"),
            patch("backend.routes.auth.get_remote_address", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-refresh-raw"

            result = await auth_mod.refresh_access_token(
                request=_make_request(user_agent="TestApp/1.0"),
                response=MagicMock(),
                body=_Body(),
            )

        assert result.token == "new-access-token-abc"
        assert result.refresh_token == new_raw_token

    async def test_invalid_refresh_token_returns_401(self):
        """Revoked / unknown refresh tokens must return 401 without distinguishing
        between the failure modes (no oracle)."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod

        with (
            patch("backend.routes.auth.lookup_refresh_token", AsyncMock(return_value=None)),
            patch("backend.routes.auth.get_remote_address", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "bad-or-revoked-token"

            with pytest.raises(HTTPException) as exc_info:
                await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

        assert exc_info.value.status_code == 401

    async def test_admin_audience_refresh_token_rejected(self):
        """Admin tokens must not be exchanged via the rider refresh endpoint —
        privilege escalation guard."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod

        with (
            patch(
                "backend.routes.auth.lookup_refresh_token",
                AsyncMock(return_value=_refresh_row(audience="admin")),
            ),
            patch("backend.routes.auth.get_remote_address", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "admin-refresh-token"

            with pytest.raises(HTTPException) as exc_info:
                await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

        assert exc_info.value.status_code == 401

    async def test_user_not_in_db_returns_401(self):
        """If the user referenced by the refresh token no longer exists, 401."""
        from fastapi import HTTPException

        from backend.routes import auth as auth_mod

        with (
            patch("backend.routes.auth.lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch("backend.routes.auth.db.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.auth.get_remote_address", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "valid-token-deleted-user"

            with pytest.raises(HTTPException) as exc_info:
                await auth_mod.refresh_access_token(request=_make_request(), response=MagicMock(), body=_Body())

        assert exc_info.value.status_code == 401

    async def test_new_token_is_minted_with_replaces_reference(self):
        """The new refresh token must reference the old row (replaces=) so the
        old token is revoked on rotation and replay attacks are blocked."""
        from backend.routes import auth as auth_mod

        issue_calls = []

        async def _capture_issue(user_id, audience, user_agent, ip, replaces):
            issue_calls.append({"replaces": replaces})
            return ("new-raw", "hashed", datetime.now(timezone.utc) + timedelta(days=30))

        with (
            patch("backend.routes.auth.lookup_refresh_token", AsyncMock(return_value=_refresh_row())),
            patch("backend.routes.auth.db.find_one", AsyncMock(return_value=_user_row())),
            patch("backend.routes.auth.issue_refresh_token", AsyncMock(side_effect=_capture_issue)),
            patch("backend.routes.auth.create_jwt_token", return_value="access-tok"),
            patch("backend.routes.auth.get_remote_address", return_value="127.0.0.1"),
        ):

            class _Body:
                refresh_token = "old-raw"

            await auth_mod.refresh_access_token(request=_make_request(user_agent="UA"), response=MagicMock(), body=_Body())

        assert issue_calls, "issue_refresh_token was not called"
        assert issue_calls[0]["replaces"] == OLD_REFRESH_ROW_ID, (
            "New token must reference the old row id so the old token is revoked on rotation"
        )
