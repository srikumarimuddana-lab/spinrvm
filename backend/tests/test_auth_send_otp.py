"""Regression tests for /auth/send-otp shadowing bug.

Context
-------
routes/auth.py used to reassign the module-level `settings` config object
(pydantic BaseSettings, has `.ENV`, `.ACCESS_TOKEN_EXPIRE_MINUTES`, etc.)
to the local `settings = await get_app_settings()` result (a dict from
the DB). After that, the function still called `settings.ENV.lower()`
and `settings.get(...)` — the first blew up when the DB returned a dict,
the second blew up when the DB call failed and `settings` fell back to
the module-level Pydantic object which has no `.get()`.

Both code paths 500'd in production for every /send-otp call, breaking
rider and driver login end-to-end. See Railway traceback:
    AttributeError: 'dict' object has no attribute 'ENV'
    AttributeError: 'Settings' object has no attribute 'get'

These tests exercise /send-otp with both branches of the DB-settings
fetch (dict returned, exception thrown) using the real production phone
number that triggered the reports: +13065203304.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PHONE = "+13065203304"


def _resolve_inner(fn):
    """slowapi's @limiter.limit wraps the coroutine in async_wrapper without
    setting __wrapped__. Walk __closure__ cells to pull out the original
    so we can call the handler without tripping rate-limit state."""
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            # slowapi closure-wrapped case: look inside __closure__ for the
            # first coroutine function.
            closure = getattr(fn, "__closure__", None) or ()
            for cell in closure:
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None:
                    if val is fn:
                        continue
                    return val
            return fn
        fn = nxt


async def _call_send_otp(db_settings_return=None, db_settings_raise=None):
    """Invoke send_otp with a mocked get_app_settings + SMS sender."""
    from backend.routes.auth import send_otp
    from backend.schemas import SendOTPRequest

    body = SendOTPRequest(phone=PHONE)
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")

    if db_settings_raise is not None:
        mocked_settings = AsyncMock(side_effect=db_settings_raise)
    else:
        mocked_settings = AsyncMock(return_value=db_settings_return)

    patches = [
        patch("backend.routes.auth.get_app_settings", mocked_settings),
        patch(
            "backend.routes.auth.send_otp_sms",
            AsyncMock(return_value={"success": True, "sid": "SMtest"}),
        ),
        patch("backend.routes.auth.db_supabase.delete_many", AsyncMock()),
        patch("backend.routes.auth.db_supabase.insert_otp_record", AsyncMock()),
        # Dev-OTP fallback is gated on ENV=development per the production-
        # hardening note in routes/auth.py — pytest.ini defaults to ENV=test
        # which (correctly) refuses the bypass, so patch it for these tests.
        patch("backend.routes.auth.settings.ENV", "development"),
    ]
    for p in patches:
        p.start()
    try:
        inner = _resolve_inner(send_otp)
        return await inner(request, body)
    finally:
        for p in patches:
            p.stop()


class TestSendOtpShadowingRegression:
    """Prove /send-otp doesn't crash when get_app_settings returns a dict
    OR raises. Phone: +13065203304 (real production report).

    Uses asyncio.run() rather than pytest-asyncio so the test runs in
    any environment without needing the plugin installed."""

    def test_db_returns_empty_dict_uses_dev_otp(self):
        """get_app_settings returns {} (no Twilio configured in DB).
        In test ENV, should fall back to dev OTP 123456 and return success."""
        result = asyncio.run(_call_send_otp(db_settings_return={}))
        assert result["success"] is True
        # Response masks phone as ***NNNN for PIPEDA compliance — never echo
        # the full E.164 number back to the client.
        assert PHONE[-4:] in result["message"]

    def test_db_returns_twilio_config_dict(self):
        """get_app_settings returns a dict WITH twilio creds — the code must
        still use .get() on the dict, not .get() on the pydantic Settings."""
        result = asyncio.run(
            _call_send_otp(
                db_settings_return={
                    "twilio_account_sid": "AC_test",
                    "twilio_auth_token": "token_test",
                    "twilio_from_number": "+10000000000",
                }
            )
        )
        assert result["success"] is True

    def test_db_settings_fetch_raises(self):
        """get_app_settings throws — the endpoint should NOT crash on
        settings.get(...) afterwards, because the shadowed local variable
        stays None rather than falling back to the module config."""
        result = asyncio.run(_call_send_otp(db_settings_raise=RuntimeError("DB down")))
        assert result["success"] is True

    def test_phone_is_normalized_in_response(self):
        """Sanity check that the E.164 phone the user provided flows through —
        only the last 4 digits should appear (PII masking)."""
        result = asyncio.run(_call_send_otp(db_settings_return={}))
        # Full number must NOT be echoed back; only the last-4 mask is allowed.
        assert PHONE not in result["message"]
        assert PHONE[-4:] in result["message"]


class TestVerifyOtpLockoutHelpers:
    """Regression for the NameError production saw:
        NameError: name '_check_otp_lockout' is not defined
    at routes/auth.py:156 inside verify_otp. The SEC-008 lockout helpers
    (_check_otp_lockout / _record_otp_failure / _clear_otp_failures) must
    exist as module-level callables so verify_otp does not crash on the
    very first call."""

    def test_lockout_helpers_exist(self):
        from backend.routes import auth

        assert callable(getattr(auth, "_check_otp_lockout", None)), (
            "_check_otp_lockout missing — verify_otp will NameError on line ~156"
        )
        assert callable(getattr(auth, "_record_otp_failure", None)), (
            "_record_otp_failure missing — verify_otp will NameError on wrong code path"
        )
        assert callable(getattr(auth, "_clear_otp_failures", None)), (
            "_clear_otp_failures missing — verify_otp will NameError on success path"
        )

    def test_check_lockout_raises_503_on_redis_error(self):
        """If Redis is down the endpoint must fail closed (503) rather than
        letting unauthenticated requests through.

        The current implementation is intentionally fail-closed:
        if we cannot consult the lockout store we cannot guarantee the
        brute-force limit is being enforced, so we return 503 to tell
        the client to retry when the store is back up.
        """
        from fastapi import HTTPException

        from backend.routes import auth

        with patch(
            "backend.routes.auth.redis_get",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            with pytest.raises(HTTPException) as excinfo:
                asyncio.run(auth._check_otp_lockout(PHONE))
        assert excinfo.value.status_code == 503

    def test_check_lockout_raises_429_when_locked(self):
        from fastapi import HTTPException

        from backend.routes import auth

        with patch("backend.routes.auth.redis_get", AsyncMock(return_value="1")):
            with pytest.raises(HTTPException) as excinfo:
                asyncio.run(auth._check_otp_lockout(PHONE))
        assert excinfo.value.status_code == 429


class TestVerifyOtpDbErrorIsNotAWrongCode:
    """C3: if get_otp_record_by_phone raises (a DB blip), verify_otp must
    surface 503 and must NOT count it as a wrong code. Otherwise a user who
    entered the CORRECT code takes a failure strike and can be locked out for
    24h (5 strikes/hour) for a fault that was entirely server-side."""

    def test_otp_db_error_raises_503_and_does_not_record_failure(self):
        from backend.routes.auth import verify_otp
        from backend.schemas import VerifyOTPRequest
        from backend.utils.error_handling import SpinrException

        body = VerifyOTPRequest(phone=PHONE, code="1234")
        request = MagicMock()
        request.client = MagicMock(host="127.0.0.1")
        response = MagicMock()

        record_failure = AsyncMock()
        with (
            # Lockout pre-check is unrelated to this path — stub it to a no-op.
            patch("backend.routes.auth._check_otp_lockout", AsyncMock(return_value=None)),
            patch("backend.routes.auth._record_otp_failure", record_failure),
            patch(
                "backend.routes.auth.db_supabase.get_otp_record_by_phone",
                AsyncMock(side_effect=RuntimeError("DB down")),
            ),
        ):
            inner = _resolve_inner(verify_otp)
            with pytest.raises(SpinrException) as excinfo:
                asyncio.run(inner(request, response, body))

        assert excinfo.value.status_code == 503
        # The correct-code-during-a-DB-blip user must NOT be penalised.
        record_failure.assert_not_called()
