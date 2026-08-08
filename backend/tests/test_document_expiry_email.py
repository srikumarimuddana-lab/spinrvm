"""Document-expiry notices must reach email, not just push.

Renewing a document means finding it, photographing it and uploading it —
work a notification that vanishes from the tray does not support. Email also
survives an uninstalled app or a stale FCM token, which for a Saskatchewan
eligibility requirement checked on every go-online is the difference between a
driver renewing in time and being suspended without warning.

The replay-safety cases matter most: the loop runs on all 18 background
replicas concurrently, so the email must inherit the same claim as the push
rather than taking one of its own.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_EXPIRED = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}


def _soon(days):
    from datetime import datetime, timedelta, timezone

    return {
        "id": "d1",
        "user_id": "u1",
        "license_expiry_date": (datetime.now(timezone.utc) + timedelta(days=days, hours=1)).isoformat(),
    }


def _run(driver, update_one_return):
    """Run one loop tick against a single driver; return (push, email) mocks."""
    from backend.utils import document_expiry as de

    def _get_rows(table, filters=None, limit=None, offset=0, **kw):
        if table == "drivers":
            return [driver] if offset == 0 else []
        return []

    push = AsyncMock()
    email = AsyncMock(return_value=True)

    with (
        patch("backend.utils.document_expiry.db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.utils.document_expiry.db.update_one", AsyncMock(return_value=update_one_return)),
        patch("backend.utils.document_expiry.send_push_notification", push),
        patch("backend.utils.document_expiry.send_lifecycle_email", email),
        patch(
            "backend.utils.document_expiry.resolve_recipient",
            AsyncMock(return_value={"id": "u1", "first_name": "Sam", "email": "sam@example.test"}),
        ),
        patch("backend.utils.document_expiry.clear_presence", AsyncMock()),
        patch("backend.utils.document_expiry.manager", MagicMock()),
    ):
        asyncio.run(de.check_expiring_documents())

    return push, email


# --- Both channels fire ----------------------------------------------------


def test_expired_suspension_emails_as_well_as_pushes():
    push, email = _run(_EXPIRED, update_one_return={"id": "d1", "status": "suspended"})
    push.assert_awaited_once()
    email.assert_awaited_once()


@pytest.mark.parametrize("days", [0, 1, 5])
def test_every_warning_tier_emails(days):
    push, email = _run(_soon(days), update_one_return={"id": "d1"})
    push.assert_awaited_once()
    email.assert_awaited_once()


# --- Replay safety: the email inherits the push's claim --------------------


def test_lost_suspension_claim_sends_neither_channel():
    # Another replica already suspended this driver on this tick.
    push, email = _run(_EXPIRED, update_one_return=None)
    push.assert_not_awaited()
    email.assert_not_awaited()


def test_lost_warning_claim_sends_neither_channel():
    # doc_expiry_warned_at CAS lost — a second replica must not duplicate mail.
    push, email = _run(_soon(5), update_one_return=None)
    push.assert_not_awaited()
    email.assert_not_awaited()


# --- Tier and content ------------------------------------------------------


def test_suspension_push_uses_the_account_tier():
    """A suspended driver can no longer earn — that must bypass the opt-out.

    On the default tier, a driver who had turned push notifications off got no
    notice at all that they'd been taken offline.
    """
    from backend.utils.driver_status_notifications import ACCOUNT_PRIORITY

    push, _ = _run(_EXPIRED, update_one_return={"id": "d1", "status": "suspended"})
    assert push.await_args.kwargs["priority"] == ACCOUNT_PRIORITY


def test_warning_push_stays_on_the_default_tier():
    # Not a block: the driver can still work until the document actually
    # expires, so this must keep honouring the push opt-out.
    push, _ = _run(_soon(5), update_one_return={"id": "d1"})
    assert "priority" not in push.await_args.kwargs


def test_expiry_email_is_transactional():
    from utils.email_notifications import EmailClass

    _, email = _run(_soon(1), update_one_return={"id": "d1"})
    assert email.await_args.kwargs["email_class"] is EmailClass.TRANSACTIONAL


def test_expiry_email_says_what_to_do_next():
    _, email = _run(_soon(1), update_one_return={"id": "d1"})
    body = email.await_args.kwargs["rendered"].text
    assert "Profile → Documents" in body


def test_expiry_email_is_branded():
    _, email = _run(_soon(1), update_one_return={"id": "d1"})
    html = email.await_args.kwargs["rendered"].html
    assert "/api/v1/branding/spinr-logo.png" in html


def test_email_failure_does_not_break_the_loop():
    from backend.utils import document_expiry as de

    def _get_rows(table, filters=None, limit=None, offset=0, **kw):
        if table == "drivers":
            return [_soon(3)] if offset == 0 else []
        return []

    push = AsyncMock()
    with (
        patch("backend.utils.document_expiry.db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.utils.document_expiry.db.update_one", AsyncMock(return_value={"id": "d1"})),
        patch("backend.utils.document_expiry.send_push_notification", push),
        patch(
            "backend.utils.document_expiry.resolve_recipient",
            AsyncMock(side_effect=RuntimeError("supabase down")),
        ),
        patch("backend.utils.document_expiry.clear_presence", AsyncMock()),
        patch("backend.utils.document_expiry.manager", MagicMock()),
    ):
        asyncio.run(de.check_expiring_documents())  # must not raise

    push.assert_awaited_once()
