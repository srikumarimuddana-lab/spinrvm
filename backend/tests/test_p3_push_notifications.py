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

    async def _register(self, existing_row=None, platform: str = "ios", token: str = "fcm-xyz"):
        from backend.routes.notifications import RegisterTokenRequest, register_push_token

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
            patch("backend.routes.notifications.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.notifications.db_supabase.update_one", AsyncMock(side_effect=_update)),
            patch("backend.routes.notifications.db_supabase.insert_one", AsyncMock(side_effect=_insert)),
            patch("backend.routes.notifications.db.update_one", AsyncMock(side_effect=_update)),
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
        assert (
            user_updates[0][2].get("fcm_token") == "fcm-xyz"
            or user_updates[0][2].get("$set", {}).get("fcm_token") == "fcm-xyz"
        )

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
            patch("backend.routes.notifications.db_supabase.get_rows", AsyncMock(return_value=notifs)),
            patch("backend.routes.notifications.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_notifications(
                limit=30,
                offset=0,
                unread_only=False,
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
            patch("backend.routes.notifications.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.notifications.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            await get_notifications(
                limit=30,
                offset=0,
                unread_only=True,
                current_user={"id": USER_ID},
            )

        assert any(f.get("is_read") is False for f in received_filters), (
            "unread_only=True did not filter by is_read=False"
        )


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

        with patch(
            "backend.routes.notifications.db_supabase.update_one",
            AsyncMock(side_effect=lambda t, q, d: updates.append((t, q, d))),
        ):
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

        with patch(
            "backend.routes.notifications.db_supabase.update_one",
            AsyncMock(side_effect=lambda t, q, d: updates.append((t, q, d))),
        ):
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

        with patch("backend.routes.notifications.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await get_preferences(current_user={"id": USER_ID})

        assert result["push_enabled"] is True
        assert result["safety_alerts"] is True

    async def test_update_prefs_partial_only_sets_non_none(self):
        from backend.routes.notifications import PreferencesUpdate, update_preferences

        req = PreferencesUpdate(promotions=False)  # only set promotions
        updates = []

        with (
            patch(
                "backend.routes.notifications.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "pref-001", "user_id": USER_ID}]),
            ),
            patch(
                "backend.routes.notifications.db_supabase.update_one",
                AsyncMock(side_effect=lambda t, q, d: updates.append(d)),
            ),
        ):
            result = await update_preferences(req=req, current_user={"id": USER_ID})

        assert result["success"] is True
        # Only promotions should be in the update payload
        assert updates[0].get("promotions") is False
        assert "push_enabled" not in updates[0], "Unset fields must not be included in partial update"


# ─────────────────────────────────────────────────────────────────────────────
# create_notification() helper — deeplink injection
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCreateNotificationHelper:
    """Pins create_notification: deeplink injected from NOTIFICATION_DEEPLINKS.

    Code under test: backend/routes/notifications.py::create_notification (~line 230).
    """

    async def test_deeplink_injected_for_known_type(self):
        from backend.routes.notifications import NOTIFICATION_DEEPLINKS, create_notification

        inserted = []

        with patch(
            "backend.routes.notifications.db_supabase.insert_one",
            AsyncMock(side_effect=lambda t, r: inserted.append(r) or r),
        ):
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

        with patch(
            "backend.routes.notifications.db_supabase.insert_one",
            AsyncMock(side_effect=lambda t, r: inserted.append(r) or r),
        ):
            notif = await create_notification(
                user_id=USER_ID,
                title="Hello",
                body="Test",
                notification_type="general",
            )

        assert "deeplink" not in notif["data"]

    async def test_caller_supplied_deeplink_not_overwritten(self):
        from backend.routes.notifications import create_notification

        with patch("backend.routes.notifications.db_supabase.insert_one", AsyncMock(return_value=None)):
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


@pytest.mark.asyncio
class TestNativePushDelivery:
    """Delivery path — firebase_admin.messaging mocked via sys.modules.

    Code under test: backend/features.py::send_push_notification (~line 1114).
    The function does a local `from firebase_admin import messaging` so we
    inject a mock into sys.modules before calling it.
    """

    async def _deliver(self, token: str):
        import sys
        from unittest.mock import MagicMock

        mock_messaging = MagicMock()
        mock_messaging.send = MagicMock(return_value="projects/spinr/messages/ok")

        mock_firebase = MagicMock()
        mock_firebase.messaging = mock_messaging

        user_row = {"id": USER_ID, "fcm_token": token}

        with (
            patch.dict(
                sys.modules,
                {
                    "firebase_admin": mock_firebase,
                    "firebase_admin.messaging": mock_messaging,
                },
            ),
            patch("backend.features.db_supabase.find_one", AsyncMock(return_value=user_row)),
            patch("backend.features.db_supabase.get_user_by_id", AsyncMock(return_value=user_row)),
        ):
            from backend import features as features_mod

            result = await features_mod.send_push_notification(
                user_id=USER_ID,
                title="Ride accepted",
                body="Your driver is on the way",
            )

        return result, mock_messaging

    async def test_push_delivered_to_ios_device(self):
        """iOS APNs-routed FCM token reaches messaging.send."""
        ios_token = "ios-apns-device-token-abc123"
        result, mock_messaging = await self._deliver(ios_token)

        assert result is True
        mock_messaging.send.assert_called_once()
        # Message was built with the correct FCM token
        msg_arg = mock_messaging.Message.call_args
        assert msg_arg is not None
        assert msg_arg.kwargs.get("token") == ios_token or (msg_arg.args and ios_token in str(msg_arg.args))

    async def test_push_delivered_to_android_device(self):
        """Android FCM registration token reaches messaging.send."""
        android_token = "android-fcm-registration-token-xyz789"
        result, mock_messaging = await self._deliver(android_token)

        assert result is True
        mock_messaging.send.assert_called_once()
        msg_arg = mock_messaging.Message.call_args
        assert msg_arg is not None
        assert msg_arg.kwargs.get("token") == android_token or (msg_arg.args and android_token in str(msg_arg.args))


# ─────────────────────────────────────────────────────────────────────────────
# Inbox persistence — send_push_notification mirrors user-facing pushes into
# the `notifications` table so the in-app bell icon populates for later viewing.
# Code under test: backend/features.py::_persist_inbox_notification
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestInboxPersistence:
    async def test_normal_push_persists_to_inbox(self):
        """A user-facing push is written to the notifications table even when
        the device has no token on file (so the inbox is the source of truth)."""
        inserted: list = []
        with (
            patch(
                "backend.features.db_supabase.insert_one",
                AsyncMock(side_effect=lambda table, row: inserted.append((table, row))),
            ),
            patch("backend.features.db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            from backend import features as features_mod

            result = await features_mod.send_push_notification(
                user_id=USER_ID,
                title="Receipt ready",
                body="Your trip receipt is ready",
                data={"type": "receipt"},
            )

        assert result is False  # no token -> delivery dropped
        assert len(inserted) == 1  # ...but inbox row still written
        table, row = inserted[0]
        assert table == "notifications"
        assert row["user_id"] == USER_ID
        assert row["type"] == "receipt"
        assert row["is_read"] is False

    async def test_ephemeral_ride_offer_not_persisted(self):
        """Time-boxed dispatch offers must not clutter the persistent inbox."""
        inserted: list = []
        with (
            patch(
                "backend.features.db_supabase.insert_one",
                AsyncMock(side_effect=lambda table, row: inserted.append(row)),
            ),
            patch("backend.features.db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            from backend import features as features_mod

            await features_mod.send_push_notification(
                user_id=USER_ID,
                title="New ride offer",
                body="Pickup 2 min away",
                data={"type": "new_ride_offer"},
            )

        assert inserted == []

    async def test_inbox_failure_does_not_block_delivery(self):
        """A failed inbox write is logged, not raised — push delivery proceeds."""
        with (
            patch(
                "backend.features.db_supabase.insert_one",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch("backend.features.db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            from backend import features as features_mod

            result = await features_mod.send_push_notification(
                user_id=USER_ID,
                title="Promo",
                body="20% off your next ride",
                data={"type": "promo"},
            )

        assert result is False  # no exception bubbled up
