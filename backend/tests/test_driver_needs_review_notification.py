"""The driver-triggered needs_review transition must tell the driver.

Editing vehicle details or re-uploading a document flips an `active` driver
to `needs_review` and forces `is_online=False`. Both paths did that silently:
the driver's only signal was the Go-online toggle refusing them later with no
explanation.
"""

from unittest.mock import AsyncMock, patch

import pytest

ACTIVE_DRIVER = {
    "id": "drv-1",
    "user_id": "usr-1",
    "status": "active",
    "is_online": True,
}


@pytest.mark.asyncio
async def test_document_upload_notifies_on_forced_offline(monkeypatch):
    import documents
    from utils import driver_status_notifications as policy

    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("features.send_push_notification", sent)

    with (
        patch("db_supabase.get_driver_by_id", AsyncMock(return_value=ACTIVE_DRIVER)),
        patch("db_supabase.update_one", AsyncMock()),
    ):
        await documents._supersede_and_flag_pending_review("drv-1", "req-1", None, flag_review=True)

    sent.assert_awaited_once()
    assert sent.await_args.args[0] == "usr-1"
    assert sent.await_args.args[1] == "Changes Under Review"
    assert "offline" in sent.await_args.args[2]
    # Informational, not an account block — honours the push opt-out.
    assert sent.await_args.kwargs["priority"] == policy.NORMAL_PRIORITY
    assert sent.await_args.kwargs["target_app"] == "driver"


@pytest.mark.asyncio
async def test_non_active_driver_is_not_flagged_or_notified(monkeypatch):
    """Only an `active` driver drops to needs_review; a pending driver
    uploading a document is just completing onboarding."""
    import documents

    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("features.send_push_notification", sent)
    pending = {**ACTIVE_DRIVER, "status": "pending"}

    with (
        patch("db_supabase.get_driver_by_id", AsyncMock(return_value=pending)),
        patch("db_supabase.update_one", AsyncMock()) as upd,
    ):
        await documents._supersede_and_flag_pending_review("drv-1", "req-1", None, flag_review=True)

    sent.assert_not_awaited()
    assert "status" not in upd.await_args.args[2]


@pytest.mark.asyncio
async def test_admin_approved_upload_does_not_notify(monkeypatch):
    """flag_review=False is the admin-uploaded-already-approved path; it must
    not take the driver offline, so there is nothing to announce."""
    import documents

    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("features.send_push_notification", sent)

    with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=ACTIVE_DRIVER)):
        await documents._supersede_and_flag_pending_review("drv-1", "req-1", None, flag_review=False)

    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_failure_does_not_break_the_transition(monkeypatch):
    """The status change is already committed — a dead FCM token must not
    surface as an error to the caller."""
    import documents

    monkeypatch.setattr("features.send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down")))

    with (
        patch("db_supabase.get_driver_by_id", AsyncMock(return_value=ACTIVE_DRIVER)),
        patch("db_supabase.update_one", AsyncMock()),
    ):
        await documents._supersede_and_flag_pending_review("drv-1", "req-1", None, flag_review=True)
