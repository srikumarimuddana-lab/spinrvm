"""
P2-13: Chat E2E — rider ↔ driver messaging (R7, D11)

Backend chat is fully implemented:
  POST /{ride_id}/messages  — persists + WS-forwards
  GET  /{ride_id}/messages  — history for participants
  GET  /{ride_id}/chat-status — availability check

These tests pin:
  - Rider sends → persisted in ride_messages, driver notified via WS
  - Driver sends → persisted, rider notified via WS
  - Non-participant cannot send (403)
  - Chat blocked on cancelled ride (400)
  - Post-trip 24h window: within → OK; expired → 400
  - GET /messages scoped to ride participants (403 for outsiders)

Store-level dedup tests live in:
  rider-app/store/__tests__/rideStore.chat.test.ts
  driver-app/store/__tests__/driverStore.chat.test.ts

Run:
    pytest backend/tests/test_p2_chat.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

RIDER_ID = "rider_p2_13"
DRIVER_USER_ID = "driver_user_p2_13"
DRIVER_ID = "driver_row_p2_13"
RIDE_ID = "ride_p2_13_001"


def _ride(status: str, **extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _driver_row(user_id: str = DRIVER_USER_ID) -> dict:
    return {"id": DRIVER_ID, "user_id": user_id}


# ─────────────────────────────────────────────────────────────────────────────
# POST /{ride_id}/messages
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSendRideMessage:
    """Pins send_ride_message: persist + WS forward.

    Code under test: backend/routes/rides.py::send_ride_message (~line 1858).
    """

    async def _send(self, ride, sender_user_id, text="Hello"):
        from backend.routes import rides as rides_mod

        ws_calls = []
        inserted = []

        class _Body:
            pass

        body = _Body()
        body.text = text

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        async def _insert(table, row):
            inserted.append(row)

        driver_row = _driver_row()

        # send_ride_message makes up to three db.find_one calls:
        #   1. get ride by id
        #   2. get driver by user_id (for is_driver auth check) — returns None for riders
        #   3. get driver by driver_id (for WS forwarding target) — only when sender is rider
        # Provide all three slots to avoid StopAsyncIteration.
        find_calls = [ride, None, driver_row if ride.get("driver_id") else None]

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(side_effect=find_calls)),
            patch("backend.routes.rides.db.insert_one", AsyncMock(side_effect=_insert)),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
        ):
            result = await rides_mod.send_ride_message(
                ride_id=RIDE_ID,
                body=body,
                current_user={"id": sender_user_id},
            )

        return result, ws_calls, inserted

    async def test_rider_sends_message_persisted(self):
        ride = _ride("in_progress")
        result, _, inserted = await self._send(ride, RIDER_ID)

        assert result["success"] is True
        assert inserted, "Message was not persisted to ride_messages"
        assert inserted[0]["text"] == "Hello"
        assert inserted[0]["sender"] == "rider"
        assert inserted[0]["ride_id"] == RIDE_ID

    async def test_rider_sends_message_notifies_driver_via_ws(self):
        ride = _ride("in_progress")
        _, ws_calls, _ = await self._send(ride, RIDER_ID)

        driver_events = [(ch, msg) for ch, msg in ws_calls if f"driver_{DRIVER_USER_ID}" in str(ch)]
        assert driver_events, "Driver was not notified via WS"
        assert driver_events[0][1]["type"] == "chat_message"
        assert driver_events[0][1]["text"] == "Hello"

    async def test_driver_sends_message_notifies_rider_via_ws(self):
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")
        ws_calls = []
        inserted = []

        class _Body:
            text = "On my way"

        async def _capture_ws(message, channel):
            ws_calls.append((channel, message))

        async def _insert(table, row):
            inserted.append(row)

        with (
            # find_one: ride, driver (for auth check)
            patch(
                "backend.routes.rides.db.find_one", AsyncMock(side_effect=[ride, _driver_row(user_id=DRIVER_USER_ID)])
            ),
            patch("backend.routes.rides.db.insert_one", AsyncMock(side_effect=_insert)),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock(side_effect=_capture_ws)),
        ):
            result = await rides_mod.send_ride_message(
                ride_id=RIDE_ID,
                body=_Body(),
                current_user={"id": DRIVER_USER_ID},
            )

        assert result["success"] is True
        assert inserted[0]["sender"] == "driver"

        rider_events = [(ch, msg) for ch, msg in ws_calls if f"rider_{RIDER_ID}" in str(ch)]
        assert rider_events, "Rider was not notified via WS after driver message"

    async def test_non_participant_cannot_send(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        class _Body:
            text = "Intruder"

        with patch(
            "backend.routes.rides.db.find_one", AsyncMock(side_effect=[ride, None])
        ):  # no driver row for outsider
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.send_ride_message(
                    ride_id=RIDE_ID,
                    body=_Body(),
                    current_user={"id": "outsider-user"},
                )

        assert exc_info.value.status_code == 403

    async def test_cancelled_ride_blocks_chat(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("cancelled")

        class _Body:
            text = "Still there?"

        with patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.send_ride_message(
                    ride_id=RIDE_ID,
                    body=_Body(),
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400
        assert "cancelled" in exc_info.value.detail.lower()

    async def test_post_trip_chat_allowed_within_24h(self):
        completed_at = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        ride = _ride("completed", ride_completed_at=completed_at)
        result, _, inserted = await self._send(ride, RIDER_ID, text="Thanks!")

        assert result["success"] is True
        assert inserted[0]["text"] == "Thanks!"

    async def test_post_trip_chat_blocked_after_24h(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        completed_at = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        ride = _ride("completed", ride_completed_at=completed_at)

        class _Body:
            text = "Too late"

        with patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.send_ride_message(
                    ride_id=RIDE_ID,
                    body=_Body(),
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 400
        assert "expired" in exc_info.value.detail.lower()


# ─────────────────────────────────────────────────────────────────────────────
# GET /{ride_id}/messages
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetRideMessages:
    """Pins get_ride_messages: history returned only to participants.

    Code under test: backend/routes/rides.py::get_ride_messages (~line 1821).
    """

    async def test_rider_can_fetch_message_history(self):
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")
        msgs = [
            {
                "id": "m1",
                "ride_id": RIDE_ID,
                "text": "Hi",
                "sender": "rider",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ]

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=[])
            ),  # no driver row for rider auth
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(side_effect=[[], msgs])),
        ):
            # Use a simpler direct approach: mock the specific calls
            call_count = [0]

            async def _get_rows(table, query, **kwargs):
                call_count[0] += 1
                if table == "drivers":
                    return []  # rider has no driver row → is_rider check passes
                if table == "ride_messages":
                    return msgs
                return []

            with patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
                with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
                    result = await rides_mod.get_ride_messages(
                        ride_id=RIDE_ID,
                        current_user={"id": RIDER_ID},
                    )

        assert result["success"] is True
        assert len(result["messages"]) == 1
        assert result["messages"][0]["text"] == "Hi"

    async def test_outsider_cannot_fetch_messages(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        async def _get_rows(table, query, **kwargs):
            return []  # outsider has no driver row

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.get_ride_messages(
                    ride_id=RIDE_ID,
                    current_user={"id": "outsider"},
                )

        assert exc_info.value.status_code == 403
