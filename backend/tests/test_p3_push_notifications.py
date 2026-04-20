"""
P3-19: Native push-notification flows (FCM/APNs)

Backend push-token + notification layer is fully implemented:
  POST /notifications/register-token  — upsert push token; mirrors to users.fcm_token
  GET  /notifications                 — paginated list, unread count
  PUT  /notifications/{id}/read       — mark single as read
  PUT  /notifications/read-all        — mark all as read
  GET  /notifications/preferences     — returns defaults if no row exists
  PUT  /notifications/preferences     — upsert preferences (partial update)
  create_notification() helper        — inserts + injects deeplink from NOTIFICATION_DEEPLINKS

The native delivery path (FCM/APNs send) cannot be tested without live
credentials; those cases are marked xfail(strict=False).

Run:
    pytest backend/tests/test_p3_push_notifications.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


USER_ID = "user_p3_19"


def _token_row(token: str = "fcm-token-abc") -> dict:
    return {
        "id": "tok-001",
        "user_id": USER_ID,
        "token": token,
        "platform": "ios",
    }


def _notif(nid: str = "notif-001", is_read: bool = False) -> dict:
    return {
        "id": nid,
        "user_id": USER_ID,
        "title": "Ride update",
        "body": "Your driver is 2 min away",
        "type": "ride_update",
        "is_read": is_read,
        "data": {},
        "created_at": "2025-01-01T00:00:00",
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /notifications/register-token
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRegisterPushToken:
    """Pins register_push_token: upsert behavior + users.fcm_token mirror.

    Code under test: backend/routes/notifications.py::register_push_token (~line 49).
    """

    async def _register(self, existing_row=None, platform: str = "ios",
                        token: str = "fcm-xyz"):
        from backend.routes.notifications import register_push_token, RegisterTokenRequest

        body = RegisterTokenRequest(token=token, platform=platform)
        updates = []
        inserted = []

        async def _get_rows(table, query=None, **kwargs):
            if table == "push_tokens":
                return [existing_row] if existing_row else []
            return []

        async def _update(table, query, data):
            updates.append((table, query, data))

        async def _insert(table, row):
            inserted.append((table, row))

        with (
            patch("backend.routes.notifications.db_supabase.get_rows",
                  AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.notifications.db_supabase.update_one",
                  AsyncMock(side_effect=_update)),
            patch("backend.routes.notifications.db_supabase.insert_one",
                  AsyncMock(side_effect=_insert)),
            patch("backend.routes.notifications.db.update_one",
                  AsyncMock(side_effect=_update)),
        ):
            result = await register_push_token(
                body=body,
                current_user={"id": USER_ID},
            )

        return result, updates, inserted

    async def test_new_token_inserted_for_new_device(self):
        result, updates, inserted = await self._register(existing_row=None)

        assert result["success"] is True
        tables_inserted = [t for t, _ in inserted]
        assert "push_tokens" in tables_inserted, "Token not inserted into push_tokens"

    async def test_existing_token_updated_not_duplicated(self):
        """When the same user/platform registers again, the row is updated,
        not duplicated."""
        result, updates, inserted = await self._register(
            existing_row=_token_row("old-token"),
            token="new-token",
        )

        assert result["success"] is True
        token_updates = [(t, q, d) for t, q, d in updates if t == "push_tokens"]
        assert token_updates, "Existing token row not updated"
        assert token_updates[0][2]["token"] == "new-token"

    async def test_token_mirrored_to_users_fcm_token(self):
        """Registration must also mirror the token to users.fcm_token so the
        send_push_notification feature path can find it without a join."""
        _, updates, _ = await self._register()

        user_updates = [(t, q, d) for t, q, d in updates if t == "users"]
        assert user_updates, "Token not mirrored to users.fcm_token"
        assert user_updates[0][2].get("fcm_token") == "fcm-xyz" or \
               user_updates[0][2].get("$set", {}).get("fcm_token") == "fcm-xyz"

    async def test_ios_and_android_stored_as_separate_rows(self):
        """One row per (user, platform) — iOS and Android tokens are distinct."""
        _, _, inserted_ios = await self._register(platform="ios", token="ios-tok")
        _, _, inserted_and = await self._register(platform="android", token="and-tok")

        # Both inserts happen; platform stored correctly
        ios_row = next((r for _, r in inserted_ios if r.get("platform") == "ios"), None)
        and_row = next((r for _, r in inserted_and if r.get("platform") == "android"), None)
        assert ios_row is not None
        assert and_row is not None


# ─────────────────────────────────────────────────────────────────────────────
# GET /notifications
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetNotifications:
    """Pins get_notifications: pagination, unread_only filter, unread_count.

    Code under test: backend/routes/notifications.py::get_notifications (~line 110).
    """

    async def test_returns_notifications_and_unread_count(self):
        from backend.routes.notifications import get_notifications

        notifs = [_notif("n1", is_read=False), _notif("n2", is_read=True)]

        with (
            patch("backend.routes.notifications.db_supabase.get_rows",
                  AsyncMock(return_value=notifs)),
            patch("backend.routes.notifications.db_supabase.count_documents",
                  AsyncMock(return_value=1)),
        ):
            result = await get_notifications(
                limit=30, offset=0, unread_only=False,
                current_user={"id": USER_ID},
            )

        assert len(result["notifications"]) == 2
        assert result["unread_count"] == 1

    async def test_unread_only_filter_applied(self):
        from backend.routes.notifications import get_notifications

        received_filters = []

        async def _get_rows(table, filters, **kwargs):
            received_filters.append(filters)
            return []

        with (
            patch("backend.routes.notifications.db_supabase.get_rows",
                  AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.notifications.db_supabase.count_documents",
                  AsyncMock(return_value=0)),
        ):
            await get_notifications(
                limit=30, offset=0, unread_only=True,
                current_user={"id": USER_ID},
            )

        assert any(f.get("is_read") is False for f in received_filters), \
            "unread_only=True did not filter by is_read=False"


# ─────────────────────────────────────────────────────────────────────────────
# PUT /notifications/{id}/read  +  read-all
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMarkNotificationsRead:
    """Pins mark_as_read and mark_all_read.

    Code under test: backend/routes/notifications.py lines ~143, ~154.
    """

    async def test_mark_single_as_read(self):
        from backend.routes.notifications import mark_as_read

        updates = []

        with patch("backend.routes.notifications.db_supabase.update_one",
                   AsyncMock(side_effect=lambda t, q, d: updates.append((t, q, d)))):
            result = await mark_as_read(
                notification_id="notif-001",
                current_user={"id": USER_ID},
            )

        assert result["success"] is True
        assert updates, "update_one not called"
        _, query, data = updates[0]
        assert query.get("id") == "notif-001"
        assert query.get("user_id") == USER_ID
        assert data.get("is_read") is True

    async def test_mark_all_read(self):
        from backend.routes.notifications import mark_all_read

        updates = []

        with patch("backend.routes.notifications.db_supabase.update_one",
                   AsyncMock(side_effect=lambda t, q, d: updates.append((t, q, d)))):
            result = await mark_all_read(current_user={"id": USER_ID})

        assert result["success"] is True
        _, query, data = updates[0]
        assert query.get("user_id") == USER_ID
        assert data.get("is_read") is True


# ─────────────────────────────────────────────────────────────────────────────
# GET /notifications/preferences  (defaults returned when no row)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestNotificationPreferences:
    """Pins preferences endpoints: defaults, partial update, upsert.

    Code under test: backend/routes/notifications.py::get_preferences (~line 165),
                     update_preferences (~line 184).
    """

    async def test_default_prefs_returned_when_no_row(self):
        from backend.routes.notifications import get_preferences

        with patch("backend.routes.notifications.db_supabase.get_rows",
                   AsyncMock(return_value=[])):
            result = await get_preferences(current_user={"id": USER_ID})

        assert result["push_enabled"] is True
        assert result["safety_alerts"] is True

    async def test_update_prefs_partial_only_sets_non_none(self):
        from backend.routes.notifications import update_preferences, PreferencesUpdate

        req = PreferencesUpdate(promotions=False)  # only set promotions
        updates = []

        with (
            patch("backend.routes.notifications.db_supabase.get_rows",
                  AsyncMock(return_value=[{"id": "pref-001", "user_id": USER_ID}])),
            patch("backend.routes.notifications.db_supabase.update_one",
                  AsyncMock(side_effect=lambda t, q, d: updates.append(d))),
        ):
            result = await update_preferences(req=req, current_user={"id": USER_ID})

        assert result["success"] is True
        # Only promotions should be in the update payload
        assert updates[0].get("promotions") is False
        assert "push_enabled" not in updates[0], \
            "Unset fields must not be included in partial update"


# ─────────────────────────────────────────────────────────────────────────────
# create_notification() helper — deeplink injection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCreateNotificationHelper:
    """Pins create_notification: deeplink injected from NOTIFICATION_DEEPLINKS.

    Code under test: backend/routes/notifications.py::create_notification (~line 230).
    """

    async def test_deeplink_injected_for_known_type(self):
        from backend.routes.notifications import create_notification, NOTIFICATION_DEEPLINKS

        inserted = []

        with patch("backend.routes.notifications.db_supabase.insert_one",
                   AsyncMock(side_effect=lambda t, r: inserted.append(r) or r)):
            notif = await create_notification(
                user_id=USER_ID,
                title="Payout complete",
                body="$50 sent to your bank",
                notification_type="payout_processed",
            )

        assert notif["data"]["deeplink"] == NOTIFICATION_DEEPLINKS["payout_processed"]

    async def test_no_deeplink_for_unknown_type(self):
        from backend.routes.notifications import create_notification

        inserted = []

        with patch("backend.routes.notifications.db_supabase.insert_one",
                   AsyncMock(side_effect=lambda t, r: inserted.append(r) or r)):
            notif = await create_notification(
                user_id=USER_ID,
                title="Hello",
                body="Test",
                notification_type="general",
            )

        assert "deeplink" not in notif["data"]

    async def test_caller_supplied_deeplink_not_overwritten(self):
        from backend.routes.notifications import create_notification

        with patch("backend.routes.notifications.db_supabase.insert_one",
                   AsyncMock(return_value=None)):
            notif = await create_notification(
                user_id=USER_ID,
                title="Custom",
                body="Custom",
                notification_type="payout_processed",
                data={"deeplink": "/custom/path"},
            )

        assert notif["data"]["deeplink"] == "/custom/path"


# ─────────────────────────────────────────────────────────────────────────────
# Native delivery path (FCM/APNs send) — requires live creds
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    strict=False,
    reason=(
        "P3-19 gap: FCM/APNs delivery requires live service-account credentials "
        "and cannot be tested in CI without a real Firebase project. "
        "Fix: add a Firebase emulator or mock FCM SDK in features/send_push_notification.py. "
        "Until then, delivery is verified manually via MOBILE_SMOKE.md §E."
    ),
)
class TestNativePushDelivery:
    """Delivery path — xfail until Firebase emulator or mock SDK is wired in."""

    def test_push_delivered_to_ios_device(self):
        raise AssertionError("Firebase emulator not configured")

    def test_push_delivered_to_android_device(self):
        raise AssertionError("Firebase emulator not configured")
