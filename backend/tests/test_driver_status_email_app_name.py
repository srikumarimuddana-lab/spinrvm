"""N17: the driver status-change email's "next step" copy uses the
`company_app_name` setting ("Open the {app_name} driver app"), not a
literal "Spinr".

Companion to test_driver_status_email.py, which covers the email fan-out
itself; this pins the fallback (unconfigured -> "Spinr", byte-for-byte) and
the configured-value path for both the action-keyed next steps
(`_EMAIL_NEXT_STEPS`) and the verification-toggle ones
(`_VERIFICATION_NEXT_STEPS`).
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.company_details as cd_mod
from utils import driver_status_notifications as policy

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_DRIVER = {"id": "drv-1", "user_id": "usr-1", "status": "active"}
_USER = {"id": "usr-1", "first_name": "Sarah", "email": "sarah@example.test"}


async def _notify(message, settings=None, driver=_DRIVER, user=_USER):
    email = AsyncMock(return_value=True)
    push = AsyncMock(return_value=True)
    loader = AsyncMock(return_value=settings or {})
    with (
        patch("utils.email_notifications.send_lifecycle_email", email),
        patch("utils.email_notifications.resolve_recipient", AsyncMock(return_value=user)),
        patch("features.send_push_notification", push),
        patch.object(cd_mod, "get_app_settings", loader),
    ):
        await policy.notify_driver_status_change(driver, message, "test")
    return email


async def test_unconfigured_app_name_reproduces_the_literal_spinr_next_step():
    email = await _notify(policy.action_message("approve"))
    body = email.await_args.kwargs["rendered"].text
    assert "Open the Spinr driver app" in body


async def test_configured_app_name_replaces_the_literal_in_the_next_step():
    email = await _notify(policy.action_message("approve"), settings={"company_app_name": "Northern Rides"})
    body = email.await_args.kwargs["rendered"].text
    assert "Open the Northern Rides driver app" in body
    assert "Spinr driver app" not in body


async def test_reject_next_step_also_uses_the_configured_app_name():
    email = await _notify(
        policy.action_message("reject", reason="blurry licence"),
        settings={"company_app_name": "Northern Rides"},
    )
    body = email.await_args.kwargs["rendered"].text
    assert "Open the Northern Rides driver app to review your documents" in body


async def test_verification_next_step_uses_the_configured_app_name():
    email = await _notify(policy.verification_message(True), settings={"company_app_name": "Northern Rides"})
    body = email.await_args.kwargs["rendered"].text
    assert "Open the Northern Rides driver app" in body
