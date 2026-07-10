"""
Tests for chat message endpoints: GET + POST /rides/{id}/messages.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio


class TestGetRideMessages:
    """Tests for GET /rides/{ride_id}/messages."""

    async def test_rider_can_get_messages(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1"}
        messages_data = [
            {"id": "m1", "ride_id": "ride_1", "text": "Hello", "sender": "rider", "timestamp": "2026-04-12T10:00:00"},
            {
                "id": "m2",
                "ride_id": "ride_1",
                "text": "On my way",
                "sender": "driver",
                "timestamp": "2026-04-12T10:01:00",
            },
        ]

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            # First call: driver lookup (user is the rider → no driver row)
            # Second call: ride_messages fetch
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=[[], messages_data])),
        ):
            from backend.routes.rides import get_ride_messages

            result = await get_ride_messages("ride_1", current_user={"id": "user_1"})
            assert result["success"] is True
            assert len(result["messages"]) == 2

    async def test_non_participant_gets_403(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1"}

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            from backend.routes.rides import get_ride_messages

            with pytest.raises(HTTPException) as exc_info:
                await get_ride_messages("ride_1", current_user={"id": "stranger"})
            assert exc_info.value.status_code == 403

    async def test_ride_not_found_returns_404(self):
        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=None)):
            from backend.routes.rides import get_ride_messages

            with pytest.raises(HTTPException) as exc_info:
                await get_ride_messages("nonexistent", current_user={"id": "user_1"})
            assert exc_info.value.status_code == 404


class TestSendRideMessage:
    """Tests for POST /rides/{ride_id}/messages."""

    async def test_rider_can_send_message(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, None, driver_row])),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        ):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="I'm at the corner")
            result = await send_ride_message("ride_1", body, current_user={"id": "user_1"})

            assert result["success"] is True
            assert result["message"]["sender"] == "rider"
            assert result["message"]["text"] == "I'm at the corner"

    async def test_non_participant_gets_403(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, None])),
        ):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="Hello")
            with pytest.raises(HTTPException) as exc_info:
                await send_ride_message("ride_1", body, current_user={"id": "stranger"})
            assert exc_info.value.status_code == 403

    async def test_ride_not_found_returns_404(self):
        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=None)):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="Hello")
            with pytest.raises(HTTPException) as exc_info:
                await send_ride_message("ride_1", body, current_user={"id": "user_1"})
            assert exc_info.value.status_code == 404

    async def test_driver_can_send_message(self):
        """Driver is a participant too — should be able to send."""
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, driver_row])),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        ):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="I've arrived!")
            result = await send_ride_message("ride_1", body, current_user={"id": "user_driver_1"})

        assert result["success"] is True
        assert result["message"]["sender"] == "driver"
        assert result["message"]["text"] == "I've arrived!"

    async def test_driver_send_notifies_rider_via_ws(self):
        """Driver message must be forwarded to rider_{rider_id}."""
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}
        ws_calls: list = []

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=[ride, driver_row])),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
        ):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            await send_ride_message("ride_1", SendMessageRequest(text="Ping"), current_user={"id": "user_driver_1"})

        assert any("rider_user_1" in str(ch) for ch, _ in ws_calls), "Rider was not notified"
        assert ws_calls[0][1]["type"] == "chat_message"

    def test_empty_message_rejected_by_model(self):
        from pydantic import ValidationError

        from backend.routes.rides import SendMessageRequest

        with pytest.raises(ValidationError):
            SendMessageRequest(text="")

    def test_message_over_500_chars_rejected(self):
        from pydantic import ValidationError

        from backend.routes.rides import SendMessageRequest

        with pytest.raises(ValidationError):
            SendMessageRequest(text="x" * 501)

    def test_500_char_message_accepted(self):
        from backend.routes.rides import SendMessageRequest

        body = SendMessageRequest(text="a" * 500)
        assert len(body.text) == 500


class TestChatPushNotifications:
    """Push notification is fired as a background task after WS broadcast."""

    async def _send(self, sender_user_id, ride, driver_row=None, sender_first_name="Alice", text="Hello there"):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from backend.routes.rides import SendMessageRequest, send_ride_message

        ws_calls: list = []
        push_calls: list = []

        async def _ws(msg, ch):
            ws_calls.append((ch, msg))

        async def _push(uid, title, body, data=None, priority="normal", target_app=None):
            push_calls.append({"uid": uid, "title": title, "body": body, "data": data, "target_app": target_app})

        find_sequence = [ride, driver_row]
        if driver_row and ride.get("driver_id"):
            find_sequence.append(driver_row)

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(side_effect=find_sequence)),
            patch("backend.routes.rides._deps.db.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock(side_effect=_ws)),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock(side_effect=_push)),
            # Use ensure_future so the coroutine actually executes on the running loop.
            patch("backend.routes.rides._deps.asyncio.create_task", side_effect=asyncio.ensure_future),
        ):
            await send_ride_message(
                "ride_1",
                SendMessageRequest(text=text),
                current_user={"id": sender_user_id, "first_name": sender_first_name},
            )
            await asyncio.sleep(0)  # yield to let the scheduled task run
        return ws_calls, push_calls

    async def test_rider_send_fires_push_to_driver(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}
        _, push_calls = await self._send("user_1", ride, driver_row=driver_row)

        assert push_calls, "No push was fired"
        p = push_calls[0]
        assert p["uid"] == "user_driver_1"
        assert p["target_app"] == "driver"
        assert "Alice" in p["title"]
        assert p["body"] == "Hello there"
        assert p["data"]["type"] == "chat_message"
        assert p["data"]["ride_id"] == "ride_1"
        assert "driver/chat" in p["data"]["deeplink"]

    async def test_driver_send_fires_push_to_rider(self):
        ride = {"id": "ride_1", "rider_id": "user_rider_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}
        _, push_calls = await self._send("user_driver_1", ride, driver_row=driver_row, sender_first_name="Bob")

        assert push_calls, "No push was fired"
        p = push_calls[0]
        assert p["uid"] == "user_rider_1"
        assert p["target_app"] == "rider"
        assert "Bob" in p["title"]
        assert "chat-driver" in p["data"]["deeplink"]

    async def test_push_body_truncated_at_100_chars(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}
        _, push_calls = await self._send("user_1", ride, driver_row=driver_row, text="x" * 200)
        assert push_calls
        assert len(push_calls[0]["body"]) <= 100

    async def test_no_push_when_no_driver_assigned(self):
        """Rider sends on a searching ride — no recipient, so no push."""
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": None, "status": "searching"}
        _, push_calls = await self._send("user_1", ride, driver_row=None)
        assert not push_calls, "Push should not fire when no driver is assigned"

    def test_push_data_values_are_strings(self):
        """FCM requires all data values to be strings."""
        data = {"type": "chat_message", "ride_id": "ride_1", "deeplink": "/chat-driver?rideId=ride_1"}
        for k, v in data.items():
            assert isinstance(v, str), f"data[{k!r}] must be a string for FCM, got {type(v)}"

    async def test_null_name_field_does_not_crash(self):
        """current_user with name=null must not raise AttributeError in sender_name resolution."""
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}
        # Simulate a partially-filled profile: name key present but value is None.
        _, push_calls = await self._send(
            "user_1",
            ride,
            driver_row=driver_row,
            sender_first_name=None,  # type: ignore[arg-type]
        )
        # Should succeed and fall back to the role default "Rider".
        assert push_calls, "Push must still fire even when first_name is None"
        assert "Rider" in push_calls[0]["title"]
