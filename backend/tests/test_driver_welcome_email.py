"""Driver welcome email: sent once, on registration (D1 in
docs/notification-channel-coverage.md — previously no push and no email at
all).
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.company_details as cd_mod
import utils.driver_emails as de_mod

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_DRIVER = {"id": "d1", "user_id": "u1"}
_USER = {"id": "u1", "first_name": "Sam", "email": "sam@example.com"}


async def _capture(**overrides):
    """Run send_driver_welcome_email with the policy layer stubbed, return the
    kwargs it handed to send_lifecycle_email."""
    send = AsyncMock(return_value=True)
    driver = overrides.pop("driver", _DRIVER)
    user = overrides.pop("user", _USER)
    with (
        patch.object(de_mod, "send_lifecycle_email", send),
        patch.object(de_mod, "resolve_recipient", AsyncMock(return_value=_USER)),
    ):
        result = await de_mod.send_driver_welcome_email(driver, user)
    return result, send


async def test_sends_with_greeting_and_transactional_class():
    result, send = await _capture()
    assert result is True
    kwargs = send.await_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["email_type"] == "driver_welcome"
    assert kwargs["email_class"] is de_mod.EmailClass.TRANSACTIONAL
    assert "Sam" in kwargs["rendered"].html


async def test_no_user_id_on_driver_row_is_a_noop():
    """A driver row with no user_id has nobody to notify — must not raise or
    attempt a send."""
    send = AsyncMock(return_value=True)
    with patch.object(de_mod, "send_lifecycle_email", send):
        result = await de_mod.send_driver_welcome_email({"id": "d1"})
    assert result is False
    send.assert_not_awaited()


async def test_sender_failure_is_swallowed():
    """Registration is already committed by the time this runs — a rendering
    or send failure must return False, never raise."""
    with patch.object(de_mod, "load_company_details", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await de_mod.send_driver_welcome_email(_DRIVER, _USER)
    assert result is False


async def test_uses_configured_app_name_in_subject_and_body():
    """Mirrors tests/test_rider_emails_app_name.py: body/subject copy must
    read from company_app_name, not a hardcoded literal."""
    send = AsyncMock(return_value=True)
    settings = {"company_app_name": "Northern Rides"}
    with (
        patch.object(de_mod, "send_lifecycle_email", send),
        patch.object(de_mod, "resolve_recipient", AsyncMock(return_value=_USER)),
        patch.object(cd_mod, "get_app_settings", AsyncMock(return_value=settings)),
    ):
        await de_mod.send_driver_welcome_email(_DRIVER, _USER)
    kwargs = send.await_args.kwargs
    assert "Northern Rides" in kwargs["subject"]
    assert "Spinr" not in kwargs["subject"]
    assert "Northern Rides" in kwargs["rendered"].text
