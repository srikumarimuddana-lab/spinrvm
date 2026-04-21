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
        assert PHONE in result["message"]

    def test_db_returns_twilio_config_dict(self):
        """get_app_settings returns a dict WITH twilio creds — the code must
        still use .get() on the dict, not .get() on the pydantic Settings."""
        result = asyncio.run(_call_send_otp(
            db_settings_return={
                "twilio_account_sid": "AC_test",
                "twilio_auth_token": "token_test",
                "twilio_from_number": "+10000000000",
            }
        ))
        assert result["success"] is True

    def test_db_settings_fetch_raises(self):
        """get_app_settings throws — the endpoint should NOT crash on
        settings.get(...) afterwards, because the shadowed local variable
        stays None rather than falling back to the module config."""
        result = asyncio.run(_call_send_otp(db_settings_raise=RuntimeError("DB down")))
        assert result["success"] is True

    def test_phone_is_normalized_in_response(self):
        """Sanity check that the E.164 phone the user provided flows through."""
        result = asyncio.run(_call_send_otp(db_settings_return={}))
        assert PHONE in result["message"]


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

    def test_check_lockout_swallows_redis_errors(self):
        """If Redis is down, login must NOT be bricked — the check should
        return silently rather than bubble the exception out of auth."""
        from backend.routes import auth

        with patch(
            "backend.routes.auth.redis_get",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            # Must NOT raise — login should still proceed if Redis is unavailable.
            asyncio.run(auth._check_otp_lockout(PHONE))

    def test_check_lockout_raises_429_when_locked(self):
        from fastapi import HTTPException

        from backend.routes import auth

        with patch("backend.routes.auth.redis_get", AsyncMock(return_value="1")):
            with pytest.raises(HTTPException) as excinfo:
                asyncio.run(auth._check_otp_lockout(PHONE))
        assert excinfo.value.status_code == 429
