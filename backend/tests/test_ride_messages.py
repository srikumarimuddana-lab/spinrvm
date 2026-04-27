"""
Tests for chat message endpoints: GET + POST /rides/{id}/messages.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestGetRideMessages:
    """Tests for GET /rides/{ride_id}/messages."""

    @pytest.mark.asyncio
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
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            # First call: driver lookup (user is the rider → no driver row)
            # Second call: ride_messages fetch
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(side_effect=[[], messages_data])),
        ):
            from backend.routes.rides import get_ride_messages

            result = await get_ride_messages("ride_1", current_user={"id": "user_1"})
            assert result["success"] is True
            assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_non_participant_gets_403(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1"}

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            from backend.routes.rides import get_ride_messages

            with pytest.raises(HTTPException) as exc_info:
                await get_ride_messages("ride_1", current_user={"id": "stranger"})
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ride_not_found_returns_404(self):
        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=None)):
            from backend.routes.rides import get_ride_messages

            with pytest.raises(HTTPException) as exc_info:
                await get_ride_messages("nonexistent", current_user={"id": "user_1"})
            assert exc_info.value.status_code == 404


class TestSendRideMessage:
    """Tests for POST /rides/{ride_id}/messages."""

    @pytest.mark.asyncio
    async def test_rider_can_send_message(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}
        driver_row = {"id": "driver_1", "user_id": "user_driver_1"}

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(side_effect=[ride, None, driver_row])),
            patch("backend.routes.rides.db.insert_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
        ):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="I'm at the corner")
            result = await send_ride_message("ride_1", body, current_user={"id": "user_1"})

            assert result["success"] is True
            assert result["message"]["sender"] == "rider"
            assert result["message"]["text"] == "I'm at the corner"

    @pytest.mark.asyncio
    async def test_non_participant_gets_403(self):
        ride = {"id": "ride_1", "rider_id": "user_1", "driver_id": "driver_1", "status": "in_progress"}

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(side_effect=[ride, None])),
        ):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="Hello")
            with pytest.raises(HTTPException) as exc_info:
                await send_ride_message("ride_1", body, current_user={"id": "stranger"})
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ride_not_found_returns_404(self):
        with patch("backend.routes.rides.db.find_one", AsyncMock(return_value=None)):
            from backend.routes.rides import SendMessageRequest, send_ride_message

            body = SendMessageRequest(text="Hello")
            with pytest.raises(HTTPException) as exc_info:
                await send_ride_message("ride_1", body, current_user={"id": "user_1"})
            assert exc_info.value.status_code == 404
