"""
SOS expired-token grace — get_current_user_allow_expired

Safety rule: SOS is never gated behind an auth refresh. A rider whose
15-minute access token lapsed mid-trip must still be able to trigger
/rides/{ride_id}/emergency. The grace path accepts a signature-valid
Spinr mobile token whose ONLY defect is expiry (bounded by
SOS_EXPIRED_TOKEN_GRACE_SECONDS) and nothing else.

These tests pin:
  - Expired-but-valid mobile token within grace → user resolved
  - Forged signature → 401 (no grace)
  - Admin-audience token → 401 (no grace)
  - token_version bump (force-logout-all) → 401 (no grace)
  - Expired beyond the grace window → 401
  - Unknown user → 401

Run:
    pytest backend/tests/test_sos_expired_token.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend import dependencies as deps
from backend.core.config import settings

USER_ID = "user_sos_grace"


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _token(
    *,
    expired_seconds_ago: int,
    aud: str | None = deps.JWT_AUD_MOBILE,
    secret: str | None = None,
    token_version: int = 0,
    user_id: str = USER_ID,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict = {
        "user_id": user_id,
        "phone": "+15550001111",
        "iat": now - timedelta(seconds=expired_seconds_ago + 900),
        "exp": now - timedelta(seconds=expired_seconds_ago),
        "token_version": token_version,
    }
    if aud is not None:
        payload["aud"] = aud
    return pyjwt.encode(payload, secret or settings.JWT_SECRET, algorithm=deps.JWT_ALGORITHM)


def _user_row(token_version: int = 0) -> dict:
    return {
        "id": USER_ID,
        "phone": "+15550001111",
        "role": "rider",
        "profile_complete": True,
        "token_version": token_version,
    }


@pytest.mark.asyncio
class TestSosExpiredTokenGrace:
    async def test_expired_token_within_grace_resolves_user(self):
        token = _token(expired_seconds_ago=600)  # lapsed 10 min ago, mid-trip
        with (
            patch.object(deps.db_supabase, "get_user_by_id", AsyncMock(return_value=_user_row())),
            patch.object(deps.db_supabase, "get_driver_by_user_id_cached", AsyncMock(return_value=None)),
        ):
            user = await deps.get_current_user_allow_expired(_creds(token))
        assert user["id"] == USER_ID
        assert user["is_driver"] is False

    async def test_forged_signature_gets_no_grace(self):
        token = _token(expired_seconds_ago=600, secret="attacker-secret-attacker-secret-32ch")
        with pytest.raises(HTTPException) as exc_info:
            await deps.get_current_user_allow_expired(_creds(token))
        assert exc_info.value.status_code == 401

    async def test_admin_audience_gets_no_grace(self):
        token = _token(expired_seconds_ago=600, aud=deps.JWT_AUD_ADMIN)
        with pytest.raises(HTTPException) as exc_info:
            await deps.get_current_user_allow_expired(_creds(token))
        assert exc_info.value.status_code == 401

    async def test_revoked_token_version_gets_no_grace(self):
        """force-logout-all bumps users.token_version — suspected compromise
        is still honoured even on the SOS path."""
        token = _token(expired_seconds_ago=600, token_version=0)
        with patch.object(deps.db_supabase, "get_user_by_id", AsyncMock(return_value=_user_row(token_version=1))):
            with pytest.raises(HTTPException) as exc_info:
                await deps.get_current_user_allow_expired(_creds(token))
        assert exc_info.value.status_code == 401

    async def test_expired_beyond_grace_window_rejected(self):
        token = _token(expired_seconds_ago=deps.SOS_EXPIRED_TOKEN_GRACE_SECONDS + 3600)
        with pytest.raises(HTTPException) as exc_info:
            await deps.get_current_user_allow_expired(_creds(token))
        assert exc_info.value.status_code == 401

    async def test_unknown_user_rejected(self):
        token = _token(expired_seconds_ago=600)
        with patch.object(deps.db_supabase, "get_user_by_id", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await deps.get_current_user_allow_expired(_creds(token))
        assert exc_info.value.status_code == 401

    async def test_missing_credentials_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            await deps.get_current_user_allow_expired(None)
        assert exc_info.value.status_code == 401
