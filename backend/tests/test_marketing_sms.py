"""Unit tests for utils.marketing_sms.send_marketing_sms.

Covers: consent gate (skip when ineligible), STOP footer appended, Twilio
credentials passed through, success/failure propagation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from utils import marketing_sms as ms
except ImportError:  # pragma: no cover
    from backend.utils import marketing_sms as ms  # type: ignore

pytestmark = pytest.mark.anyio

_USER = "44444444-4444-4444-4444-444444444444"
_SETTINGS = {
    "twilio_account_sid": "AC123",
    "twilio_auth_token": "tok",
    "twilio_from_number": "+13060000000",
}


async def test_skips_when_not_eligible():
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=False)),
        patch("utils.marketing_sms.send_sms", AsyncMock(return_value={"success": True})) as send,
    ):
        ok = await ms.send_marketing_sms(to="+13065551234", user_id=_USER, message="Deal!", log_id="rider")
    assert ok is False
    send.assert_not_called()


async def test_sends_with_stop_footer_and_credentials():
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=dict(_SETTINGS))),
        patch("utils.marketing_sms.send_sms", AsyncMock(return_value={"success": True})) as send,
    ):
        ok = await ms.send_marketing_sms(to="+13065551234", user_id=_USER, message="Deal!", log_id="rider")

    assert ok is True
    args, kwargs = send.call_args
    assert args[0] == "+13065551234"
    assert args[1].endswith("Reply STOP to unsubscribe.")
    assert "Deal!" in args[1]
    assert kwargs["twilio_from"] == "+13060000000"


async def test_returns_false_on_provider_failure():
    with (
        patch("services.marketing_consent.is_eligible", AsyncMock(return_value=True)),
        patch("settings_loader.get_app_settings", AsyncMock(return_value=dict(_SETTINGS))),
        patch("utils.marketing_sms.send_sms", AsyncMock(return_value={"success": False, "provider": "twilio"})),
    ):
        ok = await ms.send_marketing_sms(to="+13065551234", user_id=_USER, message="Deal!")
    assert ok is False
