"""
Tests for PATCH /rides/{id}/notes — rider-attached post-confirm note.

Endpoint replaces the legacy "note for driver" field on the booking
screen. Allowed only while the driver is still en route; locked once
the trip starts (in_progress). Backed by ride_notes_updated WS event
to the assigned driver.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _ride(status: str = "driver_assigned", rider_id: str = "rider_1", driver_id: str = "driver_1"):
    return {
        "id": "ride_notes_1",
        "rider_id": rider_id,
        "driver_id": driver_id,
        "status": status,
    }


class TestRideNotesPatch:
    @pytest.mark.asyncio
    async def test_rider_updates_notes_pre_pickup(self):
        """Rider can attach a note while driver is en route."""
        from backend.routes import rides as rides_mod

        ride = _ride(status="driver_assigned")
        send_mock = AsyncMock()
        update_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.manager.send_personal_message", send_mock),
        ):
            mock_db.find_one = AsyncMock(side_effect=[ride, {"id": "driver_1", "user_id": "user_driver_1"}])
            mock_db.update_one = update_mock

            result = await rides_mod.patch_ride_notes(
                "ride_notes_1",
                rides_mod.RideNotesUpdateRequest(notes="Gate code 4521"),
                request=None,
                current_user={"id": "rider_1"},
            )

        assert result == {"success": True, "notes": "Gate code 4521"}
        # Ride row written with the trimmed note
        update_mock.assert_called_once()
        update_args = update_mock.call_args
        assert update_args.args[0] == "rides"
        assert update_args.args[1] == {"id": "ride_notes_1"}
        assert update_args.args[2]["$set"]["rider_notes"] == "Gate code 4521"
        # WS event fanned out to the assigned driver
        send_mock.assert_called_once()
        msg, channel = send_mock.call_args.args[0], send_mock.call_args.args[1]
        assert msg["type"] == "ride_notes_updated"
        assert msg["ride_id"] == "ride_notes_1"
        assert msg["notes"] == "Gate code 4521"
        assert channel == "driver_user_driver_1"

    @pytest.mark.asyncio
    async def test_empty_string_clears_note(self):
        """Empty/whitespace input clears the column to NULL."""
        from backend.routes import rides as rides_mod

        ride = _ride(status="driver_assigned", driver_id=None)
        update_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        ):
            mock_db.find_one = AsyncMock(return_value=ride)
            mock_db.update_one = update_mock

            result = await rides_mod.patch_ride_notes(
                "ride_notes_1",
                rides_mod.RideNotesUpdateRequest(notes="   "),
                request=None,
                current_user={"id": "rider_1"},
            )

        assert result == {"success": True, "notes": None}
        assert update_mock.call_args.args[2]["$set"]["rider_notes"] is None

    @pytest.mark.asyncio
    async def test_non_rider_forbidden(self):
        """A user other than the rider gets 403."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride(status="driver_accepted")
        with (
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        ):
            mock_db.find_one = AsyncMock(return_value=ride)
            mock_db.update_one = AsyncMock()

            with pytest.raises(HTTPException) as ei:
                await rides_mod.patch_ride_notes(
                    "ride_notes_1",
                    rides_mod.RideNotesUpdateRequest(notes="hi"),
                    request=None,
                    current_user={"id": "someone_else"},
                )
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_after_pickup_locked(self):
        """Once the ride is in_progress the note is locked (409)."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride(status="in_progress")
        with (
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        ):
            mock_db.find_one = AsyncMock(return_value=ride)
            mock_db.update_one = AsyncMock()

            with pytest.raises(HTTPException) as ei:
                await rides_mod.patch_ride_notes(
                    "ride_notes_1",
                    rides_mod.RideNotesUpdateRequest(notes="too late"),
                    request=None,
                    current_user={"id": "rider_1"},
                )
        assert ei.value.status_code == 409

    @pytest.mark.asyncio
    async def test_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with (
            patch("backend.routes.rides._deps.db") as mock_db,
        ):
            mock_db.find_one = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as ei:
                await rides_mod.patch_ride_notes(
                    "missing",
                    rides_mod.RideNotesUpdateRequest(notes="hi"),
                    request=None,
                    current_user={"id": "rider_1"},
                )
        assert ei.value.status_code == 404
