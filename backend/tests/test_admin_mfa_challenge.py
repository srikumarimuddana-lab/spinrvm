"""MFA challenge token hardening — audience pin + no error-detail oracle.

Pins:
  - _mint_mfa_challenge_token carries the dedicated mfa-challenge audience
    and round-trips through the /mfa/challenge decode.
  - Tokens without that audience (aud-less legacy, rider, admin access) are
    rejected with the static "Invalid MFA token" detail — PyJWT's specific
    error string (which fingerprints the failing claim) must never reach
    the client.

Run:
    pytest backend/tests/test_admin_mfa_challenge.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from backend.core.config import settings
from backend.routes.admin import auth as admin_auth


def _decode_like_endpoint(token: str) -> dict:
    """Mirror the exact decode performed by admin_mfa_challenge."""
    return pyjwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.ALGORITHM],
        audience=admin_auth.JWT_AUD_MFA_CHALLENGE,
    )


def test_minted_challenge_token_round_trips():
    token = admin_auth._mint_mfa_challenge_token("staff-001")
    payload = _decode_like_endpoint(token)
    assert payload["type"] == "mfa_challenge"
    assert payload["user_id"] == "staff-001"
    assert payload["aud"] == admin_auth.JWT_AUD_MFA_CHALLENGE


@pytest.mark.parametrize(
    "claims",
    [
        # aud-less token (e.g. forged or pre-audience legacy)
        {"type": "mfa_challenge", "user_id": "staff-001"},
        # rider access token audience
        {"type": "mfa_challenge", "user_id": "staff-001", "aud": "spinr:rider"},
        # full admin access token audience — proof of password must not be
        # interchangeable with an admin session and vice versa
        {"type": "mfa_challenge", "user_id": "staff-001", "aud": "spinr:admin"},
    ],
)
def test_wrong_or_missing_audience_rejected(claims):
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {**claims, "exp": now + timedelta(minutes=5)},
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(pyjwt.InvalidTokenError):
        _decode_like_endpoint(token)


@pytest.mark.anyio
async def test_endpoint_returns_static_detail_for_bad_token():
    from starlette.requests import Request as StarletteRequest

    class _Body:
        mfa_token = "garbage.token.value"
        totp_code = "000000"

    request = StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/auth/mfa/challenge",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_auth.admin_mfa_challenge(request=request, body=_Body())

    assert exc_info.value.status_code == 401
    # Exact static string — any suffix would be a PyJWT error-detail oracle.
    assert exc_info.value.detail == "Invalid MFA token"
