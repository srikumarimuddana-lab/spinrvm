"""
P2-14: SOS E2E — in-ride emergency button (R13)

Backend emergency endpoint is fully implemented:
  POST /{ride_id}/emergency — persists incident, notifies admin WS,
                               attempts emergency-contact SMS

These tests pin:
  - Rider triggers SOS → incident persisted in emergencies table
  - Rider triggers SOS → admin dashboard notified via WS
  - Driver triggers SOS → persisted with role="driver"
  - Non-participant cannot trigger SOS (403)
  - Unknown ride returns 404
  - Response includes incident_id (unique UUID per trigger)
  - Lat/lon forwarded to incident record

Run:
    pytest backend/tests/test_p2_sos.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

RIDER_ID = "rider_p2_14"
DRIVER_USER_ID = "driver_user_p2_14"
DRIVER_ID = "driver_row_p2_14"
RIDE_ID = "ride_p2_14_001"


def _ride(status: str = "in_progress") -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
    }


def _driver_row(user_id: str = DRIVER_USER_ID) -> dict:
    return {"id": DRIVER_ID, "user_id": user_id}


class _Req:
    message = "Emergency!"
    latitude = 43.651070
    longitude = -79.347015


# ─────────────────────────────────────────────────────────────────────────────
# POST /{ride_id}/emergency
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestTriggerEmergency:
    """Pins trigger_emergency: persist + WS notify admin.

    Code under test: backend/routes/rides.py::trigger_emergency (~line 1661).
    """

    async def _trigger(self, sender_user_id: str, driver_row=None, ride=None):
        from backend.routes import rides as rides_mod

        persisted = []
        ws_calls = []

        async def _insert(table, row):
            persisted.append((table, row))

        async def _ws(message, channel):
            ws_calls.append((channel, message))

        async def _broadcast_admins(message):
            ws_calls.append(("admin_broadcast", message))

        async def _get_rows(table, query, **kwargs):
            if table == "drivers":
                return [driver_row] if driver_row else []
            if table == "emergency_contacts":
                return []
            return []

        active_ride = ride if ride is not None else _ride()

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=active_ride)),
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.rides.db_supabase.insert_one", AsyncMock(side_effect=_insert)),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock(side_effect=_ws)),
            patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock(side_effect=_broadcast_admins)),
            patch(
                "backend.routes.rides.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
            ),
        ):
            result = await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                request=_Req(),
                current_user={"id": sender_user_id},
            )

        return result, persisted, ws_calls

    async def test_rider_sos_incident_persisted(self):
        result, persisted, _ = await self._trigger(RIDER_ID)

        assert result["success"] is True
        assert persisted, "Incident not persisted"
        table, row = persisted[0]
        assert table == "emergencies"
        assert row["ride_id"] == RIDE_ID
        assert row["reported_by_user_id"] == RIDER_ID
        assert row["role"] == "rider"

    async def test_rider_sos_admin_notified_via_ws(self):
        _, _, ws_calls = await self._trigger(RIDER_ID)

        admin_events = [(ch, msg) for ch, msg in ws_calls if "admin" in str(ch)]
        assert admin_events, "Admin was not notified via WS"
        assert admin_events[0][1]["type"] == "emergency_alert"
        assert admin_events[0][1]["incident"]["ride_id"] == RIDE_ID

    async def test_driver_sos_persisted_with_driver_role(self):
        result, persisted, _ = await self._trigger(
            DRIVER_USER_ID,
            driver_row=_driver_row(),
        )

        assert result["success"] is True
        _, row = persisted[0]
        assert row["role"] == "driver"
        assert row["reported_by_user_id"] == DRIVER_USER_ID

    async def test_non_participant_cannot_trigger_sos(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with (
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=_ride())),
            patch("backend.routes.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.trigger_emergency(
                    ride_id=RIDE_ID,
                    request=_Req(),
                    current_user={"id": "outsider-user"},
                )

        assert exc_info.value.status_code == 403

    async def test_unknown_ride_returns_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.trigger_emergency(
                    ride_id="nonexistent-ride",
                    request=_Req(),
                    current_user={"id": RIDER_ID},
                )

        assert exc_info.value.status_code == 404

    async def test_response_contains_unique_incident_id(self):
        result1, _, _ = await self._trigger(RIDER_ID)
        result2, _, _ = await self._trigger(RIDER_ID)

        assert result1["incident_id"] != result2["incident_id"]
        # Must be a valid UUID
        UUID(result1["incident_id"])
        UUID(result2["incident_id"])

    async def test_lat_lon_forwarded_to_incident_record(self):
        _, persisted, _ = await self._trigger(RIDER_ID)

        _, row = persisted[0]
        assert row["latitude"] == _Req.latitude
        assert row["longitude"] == _Req.longitude
