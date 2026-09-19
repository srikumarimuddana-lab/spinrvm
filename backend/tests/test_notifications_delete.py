"""
Notification-inbox revamp (2026-09-19): DELETE /notifications/{id},
DELETE /notifications (with optional ?read_only=true), and the best-effort
`new_notification` WebSocket push from create_notification().

Existing endpoints (GET, PUT .../read, PUT .../read-all, preferences) are
covered by test_p3_push_notifications.py and test_notification_preferences.py
and are intentionally not re-tested here.

Run:
    pytest backend/tests/test_notifications_delete.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.asyncio]

USER_ID = "user-delete-test"
OTHER_USER_ID = "someone-else"

try:
    import routes.notifications as notifications_module
    from routes.notifications import (
        clear_notifications,
        create_notification,
        delete_notification,
    )

    _MOD = "routes.notifications"
except ImportError:  # pragma: no cover
    import backend.routes.notifications as notifications_module  # type: ignore
    from backend.routes.notifications import (  # type: ignore
        clear_notifications,
        create_notification,
        delete_notification,
    )

    _MOD = "backend.routes.notifications"


async def _flush_ws_push_tasks() -> None:
    """create_notification() now fires its WS push as a background
    asyncio.create_task (non-blocking for the caller) rather than awaiting
    it inline — wait for whatever it scheduled to finish before asserting
    on the mocked send call."""
    pending = list(notifications_module._background_ws_tasks)
    if pending:
        await asyncio.gather(*pending)


def _notif(nid: str = "notif-1", user_id: str = USER_ID, is_read: bool = False) -> dict:
    return {
        "id": nid,
        "user_id": user_id,
        "title": "Ride update",
        "body": "Your driver is arriving",
        "type": "ride_update",
        "is_read": is_read,
        "data": {},
        "created_at": "2026-01-01T00:00:00",
    }


class TestDeleteOneNotification:
    async def test_deletes_when_owned(self):
        with (
            patch(f"{_MOD}.db_supabase.get_rows", AsyncMock(return_value=[_notif()])),
            patch(f"{_MOD}.db_supabase.delete_many", AsyncMock(return_value=[_notif()])) as delete_mock,
        ):
            res = await delete_notification("notif-1", current_user={"id": USER_ID})

        assert res == {"success": True}
        delete_mock.assert_awaited_once_with("notifications", {"id": "notif-1", "user_id": USER_ID})

    async def test_404_when_not_found_or_not_owned(self):
        """A notification that doesn't exist, or belongs to another user, must
        never be deletable — get_rows is scoped to {id, user_id}, so another
        user's row simply never matches and comes back empty here."""
        with (
            patch(f"{_MOD}.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch(f"{_MOD}.db_supabase.delete_many", AsyncMock()) as delete_mock,
        ):
            with pytest.raises(Exception) as exc_info:
                await delete_notification("notif-1", current_user={"id": USER_ID})

        assert getattr(exc_info.value, "status_code", None) == 404
        delete_mock.assert_not_awaited()


class TestClearNotifications:
    async def test_clears_all_by_default(self):
        with patch(f"{_MOD}.db_supabase.delete_many", AsyncMock(return_value=[])) as delete_mock:
            res = await clear_notifications(read_only=False, current_user={"id": USER_ID})

        assert res == {"success": True}
        delete_mock.assert_awaited_once_with("notifications", {"user_id": USER_ID})

    async def test_clears_read_only_when_flagged(self):
        with patch(f"{_MOD}.db_supabase.delete_many", AsyncMock(return_value=[])) as delete_mock:
            res = await clear_notifications(read_only=True, current_user={"id": USER_ID})

        assert res == {"success": True}
        delete_mock.assert_awaited_once_with("notifications", {"user_id": USER_ID, "is_read": True})


class TestNewNotificationWSPush:
    """create_notification() must always write the DB row regardless of WS
    delivery outcome, and never raise on a WS-send failure."""

    async def test_db_write_happens_even_if_ws_send_raises(self):
        insert_mock = AsyncMock(return_value=None)
        with (
            patch(f"{_MOD}.db_supabase.insert_one", insert_mock),
            patch(f"{_MOD}.db_supabase.count_documents", AsyncMock(return_value=3)),
            patch("socket_manager.manager.send_personal_message", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            notif = await create_notification(USER_ID, "Title", "Body", "general")
            await _flush_ws_push_tasks()

        insert_mock.assert_awaited_once()
        assert notif["user_id"] == USER_ID
        assert notif["title"] == "Title"

    async def test_ws_push_sent_to_both_rider_and_driver_keys(self):
        send_mock = AsyncMock(return_value=None)
        with (
            patch(f"{_MOD}.db_supabase.insert_one", AsyncMock(return_value=None)),
            patch(f"{_MOD}.db_supabase.count_documents", AsyncMock(return_value=2)),
            patch("socket_manager.manager.send_personal_message", send_mock),
        ):
            await create_notification(USER_ID, "Title", "Body", "general")
            await _flush_ws_push_tasks()

        assert send_mock.await_count == 2
        sent_client_ids = {call.args[1] for call in send_mock.await_args_list}
        assert sent_client_ids == {f"rider_{USER_ID}", f"driver_{USER_ID}"}
        for call in send_mock.await_args_list:
            payload = call.args[0]
            assert payload["type"] == "new_notification"
            assert payload["unread_count"] == 2
            assert payload["notification"]["user_id"] == USER_ID

    async def test_count_documents_failure_falls_back_without_raising(self):
        with (
            patch(f"{_MOD}.db_supabase.insert_one", AsyncMock(return_value=None)),
            patch(f"{_MOD}.db_supabase.count_documents", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("socket_manager.manager.send_personal_message", AsyncMock(return_value=None)) as send_mock,
        ):
            notif = await create_notification(USER_ID, "Title", "Body", "general")
            await _flush_ws_push_tasks()

        assert notif["user_id"] == USER_ID
        for call in send_mock.await_args_list:
            assert call.args[0]["unread_count"] == 1
