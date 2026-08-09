"""Approving a driver's last pending document must tell them.

This transition was silent. The rejection path notified; the approval path —
which can flip a driver from `needs_review` straight back to `active` — sent
nothing, so a driver re-activated by an admin learned it only by discovering
the Go-online toggle had started working again.
"""

from unittest.mock import AsyncMock, patch

import pytest

import routes.admin.documents as admin_docs

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_DOC = {"id": "doc-1", "driver_id": "drv-1", "document_type": "Insurance", "status": "pending"}
_NEEDS_REVIEW = {"id": "drv-1", "user_id": "usr-1", "status": "needs_review"}
_ADMIN = {"id": "adm-1", "email": "admin@spinr.ca", "role": "super_admin"}


async def _approve(driver, remaining_pending=None):
    """Run the review endpoint's approval path with the DB stubbed."""
    notify = AsyncMock(return_value=True)

    async def _get_rows(table, filters=None, **kwargs):
        if table == "driver_documents" and (filters or {}).get("status") == "pending":
            return remaining_pending or []
        if table == "driver_documents":
            return [_DOC]
        return []

    with (
        patch.object(admin_docs.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(admin_docs.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_docs.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(admin_docs, "notify_driver_status_change", notify),
        patch.object(admin_docs, "_log_driver_activity", AsyncMock()),
        patch.object(admin_docs, "log_admin_action", AsyncMock()),
        patch.object(admin_docs, "supabase", None),
    ):
        await admin_docs.admin_review_driver_document(
            "doc-1",
            admin_docs.DocumentReviewRequest(status="approved"),
            admin=_ADMIN,
        )
    return notify


async def test_last_approval_reactivates_and_notifies():
    notify = await _approve(_NEEDS_REVIEW)
    notify.assert_awaited_once()
    message = notify.await_args.args[1]
    assert message["title"] == "You're Approved! 🎉"
    # Reaches email too, not only push — the whole point of routing it
    # through the shared policy rather than sending inline.
    assert message["email"] is not None


async def test_no_notice_while_documents_are_still_pending():
    # The driver stays in needs_review; there is nothing to announce yet.
    notify = await _approve(_NEEDS_REVIEW, remaining_pending=[{"id": "doc-2"}])
    notify.assert_not_awaited()


async def test_no_notice_when_driver_was_not_in_needs_review():
    # An already-active driver's status did not change, so a "You're Approved"
    # notice would be a lie about what just happened.
    notify = await _approve({**_NEEDS_REVIEW, "status": "active"})
    notify.assert_not_awaited()


async def test_db_failure_does_not_fail_the_approval():
    """The document approval itself is committed and audited regardless."""
    with (
        patch.object(admin_docs.db_supabase, "get_rows", AsyncMock(return_value=[_DOC])),
        patch.object(admin_docs.db_supabase, "update_one", AsyncMock()),
        patch.object(
            admin_docs.db_supabase,
            "get_driver_by_id",
            AsyncMock(side_effect=RuntimeError("supabase down")),
        ),
        patch.object(admin_docs, "notify_driver_status_change", AsyncMock()),
        patch.object(admin_docs, "_log_driver_activity", AsyncMock()),
        patch.object(admin_docs, "log_admin_action", AsyncMock()) as audit,
        patch.object(admin_docs, "supabase", None),
    ):
        result = await admin_docs.admin_review_driver_document(
            "doc-1",
            admin_docs.DocumentReviewRequest(status="approved"),
            admin=_ADMIN,
        )
    assert result is not None
    audit.assert_awaited_once()
