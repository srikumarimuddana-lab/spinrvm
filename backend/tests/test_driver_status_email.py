"""Driver lifecycle notices must reach email, not just push.

Push is lossy and transient — an uninstalled app, a revoked token, or a stale
FCM registration means an approval or a suspension simply never lands, and
nothing tells us. These cover the email fan-out added to the single sender in
utils/driver_status_notifications.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

from utils import driver_status_notifications as policy

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_DRIVER = {"id": "drv-1", "user_id": "usr-1", "status": "active"}
_USER = {"id": "usr-1", "first_name": "Sarah", "email": "sarah@example.test"}


async def _notify(message, driver=_DRIVER, user=_USER):
    """Drive the real sender with both channels stubbed at their boundaries."""
    email = AsyncMock(return_value=True)
    push = AsyncMock(return_value=True)
    with (
        patch("utils.email_notifications.send_lifecycle_email", email),
        patch("utils.email_notifications.resolve_recipient", AsyncMock(return_value=user)),
        patch("features.send_push_notification", push),
    ):
        result = await policy.notify_driver_status_change(driver, message, "test")
    return result, email, push


# --- Which statuses email --------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject", "suspend", "ban"])
async def test_account_decisions_send_an_email(action):
    _, email, push = await _notify(policy.action_message(action, reason="docs expired"))
    email.assert_awaited_once()
    push.assert_awaited_once(), "email must not replace the push"


async def test_needs_review_stays_push_only():
    # Fires on every vehicle edit and document re-upload — emailing it would
    # turn routine self-service into inbox noise.
    _, email, push = await _notify(policy.status_message("needs_review"))
    email.assert_not_awaited()
    push.assert_awaited_once()


async def test_email_statuses_matches_the_documented_set():
    assert policy.EMAIL_STATUSES == {"active", "rejected", "suspended", "banned"}
    assert "needs_review" not in policy.EMAIL_STATUSES
    assert "pending" not in policy.EMAIL_STATUSES


# --- Content ---------------------------------------------------------------


async def test_subject_mirrors_the_push_title():
    # A driver seeing both must recognise them as one notice, not two events.
    message = policy.action_message("suspend", reason="documents expired")
    _, email, push = await _notify(message)
    assert email.await_args.kwargs["subject"] == message["title"]
    assert push.await_args.args[1] == message["title"]


async def test_email_carries_the_admin_reason():
    _, email, _ = await _notify(policy.action_message("suspend", reason="insurance lapsed"))
    body = email.await_args.kwargs["rendered"].text
    assert "insurance lapsed" in body


async def test_ban_email_withholds_the_admin_reason():
    # Ban reasons are internal admin text ("fraud ring #4412"), not vetted
    # customer-facing copy — same rule the push already follows.
    _, email, _ = await _notify(policy.action_message("ban", reason="fraud ring #4412"))
    body = email.await_args.kwargs["rendered"].text
    assert "fraud ring" not in body
    assert "support@spinr.ca" in body


async def test_email_adds_a_next_step_the_push_has_no_room_for():
    _, email, _ = await _notify(policy.action_message("reject", reason="blurry licence"))
    body = email.await_args.kwargs["rendered"].text
    assert "submit them again" in body


async def test_email_greets_by_first_name_when_known():
    _, email, _ = await _notify(policy.action_message("approve"))
    assert "Hi Sarah," in email.await_args.kwargs["rendered"].text


async def test_email_omits_the_greeting_when_no_name_on_file():
    _, email, _ = await _notify(policy.action_message("approve"), user={"id": "usr-1", "email": "x@y.test"})
    assert "Hi ," not in email.await_args.kwargs["rendered"].text


async def test_email_is_branded():
    _, email, _ = await _notify(policy.action_message("approve"))
    html = email.await_args.kwargs["rendered"].html
    assert "/api/v1/branding/spinr-logo.png" in html
    assert policy.EMAIL_STATUSES  # sanity
    assert "#FF3B30" in html


async def test_email_is_transactional_class():
    # A driver must not be able to opt out of being told they were suspended.
    from utils.email_notifications import EmailClass

    _, email, _ = await _notify(policy.action_message("suspend"))
    assert email.await_args.kwargs["email_class"] is EmailClass.TRANSACTIONAL


# --- Contract --------------------------------------------------------------


async def test_email_failure_does_not_suppress_the_push():
    push = AsyncMock(return_value=True)
    with (
        patch("utils.email_notifications.send_lifecycle_email", AsyncMock(side_effect=RuntimeError("SES down"))),
        patch("utils.email_notifications.resolve_recipient", AsyncMock(return_value=_USER)),
        patch("features.send_push_notification", push),
    ):
        result = await policy.notify_driver_status_change(_DRIVER, policy.action_message("approve"), "test")
    assert result is True, "push delivery is the return value; email must not change it"
    push.assert_awaited_once()


async def test_return_value_still_reflects_push_only():
    email = AsyncMock(return_value=True)
    with (
        patch("utils.email_notifications.send_lifecycle_email", email),
        patch("utils.email_notifications.resolve_recipient", AsyncMock(return_value=_USER)),
        patch("features.send_push_notification", AsyncMock(return_value=False)),
    ):
        result = await policy.notify_driver_status_change(_DRIVER, policy.action_message("approve"), "test")
    assert result is False
    email.assert_awaited_once(), "a failed push must not stop the email"


async def test_soft_deleted_driver_gets_neither_channel():
    _, email, push = await _notify(
        policy.action_message("approve"),
        driver={**_DRIVER, "deleted_at": "2026-01-01T00:00:00Z"},
    )
    email.assert_not_awaited()
    push.assert_not_awaited()
