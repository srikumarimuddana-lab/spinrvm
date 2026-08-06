"""An expired access token must not mint a Sentry event.

server.py bridges loguru into Sentry at ERROR only
(``logger.add(_loguru_sentry_sink, level="ERROR")``), so the log level chosen in
``get_current_user``'s JWT-rejection handler *is* the Sentry filter. It used to
be ``logger.error``, which meant every routine token expiry — a 15-min
rider/driver token or a 1-hr admin token ageing out mid-session, after which the
client silently refreshes and retries — raised "JWT verification failed: 401:
Token has expired" in Sentry.

These tests pin the level per rejection reason so the noise cannot come back:

    expired  → info     (routine, self-healing)
    invalid  → warning  (bad signature / malformed — inspectable, not paging)
    other    → error    (genuine defect: every request is failing auth)
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

pytestmark = pytest.mark.asyncio

_SECRET = "unit-test-jwt-secret-at-least-32-chars-long"


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _token(*, expired: bool = False, secret: str = _SECRET) -> str:
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=15)
    return jwt.encode(
        {"user_id": "user-1", "phone": "+13065550100", "aud": "spinr:rider", "iat": now, "exp": exp},
        secret,
        algorithm="HS256",
    )


async def _reject(token: str, *, verify_side_effect=None):
    """Drive get_current_user to its JWT-rejection handler.

    Returns ``(HTTPException, mocked logger, mocked metric counter)``. Firebase
    is forced to decline (ValueError) so the token always falls through to the
    JWT branch.
    """
    from dependencies import get_current_user

    with ExitStack() as stack:
        stack.enter_context(
            patch("dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not a firebase token"))
        )
        stack.enter_context(patch("dependencies.settings.JWT_SECRET", _SECRET))
        log = stack.enter_context(patch("dependencies.logger", MagicMock()))
        metric = stack.enter_context(patch("dependencies._metric_inc", MagicMock()))
        if verify_side_effect is not None:
            stack.enter_context(patch("dependencies.verify_jwt_token", side_effect=verify_side_effect))

        with pytest.raises(HTTPException) as exc:
            await get_current_user(_creds(token))
    return exc.value, log, metric


async def test_expired_token_logs_info_not_error():
    exc, log, metric = await _reject(_token(expired=True))

    assert exc.status_code == 401
    # C4 unchanged: the client still learns only that the token is invalid.
    assert exc.detail == "Invalid token"
    # The regression: an expired token must never reach the ERROR sink (Sentry).
    log.error.assert_not_called()
    log.info.assert_called_once()
    assert "Token has expired" in log.info.call_args.args[0]
    metric.assert_called_once_with("spinr_auth_token_rejected_total", {"reason": "expired"})


async def test_tampered_signature_logs_warning_not_error():
    exc, log, metric = await _reject(_token(secret="a-different-secret-also-32-chars-x"))

    assert exc.status_code == 401
    assert exc.detail == "Invalid token"
    log.error.assert_not_called()
    log.warning.assert_called_once()
    metric.assert_called_once_with("spinr_auth_token_rejected_total", {"reason": "invalid"})


async def test_malformed_token_logs_warning_not_error():
    exc, log, metric = await _reject("this-is-not-a-jwt-at-all")

    assert exc.status_code == 401
    log.error.assert_not_called()
    log.warning.assert_called_once()
    metric.assert_called_once_with("spinr_auth_token_rejected_total", {"reason": "invalid"})


async def test_unexpected_verify_failure_still_logs_error():
    """A non-HTTPException out of verify_jwt_token is a real defect (bad
    JWT_SECRET type, PyJWT internal error) — auth is failing for *everyone*, so
    it must keep paging via Sentry."""
    exc, log, metric = await _reject(
        _token(),
        verify_side_effect=TypeError("Expected a string value"),
    )

    assert exc.status_code == 401
    assert exc.detail == "Invalid token"
    log.error.assert_called_once()
    assert "TypeError" in log.error.call_args.args[0]
    metric.assert_called_once_with("spinr_auth_token_rejected_total", {"reason": "error"})


async def test_rejection_log_never_contains_the_signing_secret():
    """The handler interpolates the exception, never the credential."""
    for token in (_token(expired=True), "not-a-jwt", _token(secret="another-secret-that-is-32-chars-ok")):
        _, log, _metric = await _reject(token)
        emitted = " ".join(
            str(call.args) for level in (log.info, log.warning, log.error) for call in level.call_args_list
        )
        assert _SECRET not in emitted
