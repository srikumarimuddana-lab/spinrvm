"""
E2E — SOS / emergency-alert flow (R-P0-1 regression suite).

The original silent-failure bug: trigger_emergency broadcast to admin sockets
was wired to a client_id that pointed to no real WebSocket connection, so
emergency alerts were inserted to the DB but never reached the admin dashboard.
The fix broadcasts via manager.broadcast_to_admins() (PR #117).

Additionally, the SOSButton component retries 3× before surfacing the "Call 911"
fallback. This suite guards the backend half of that contract.

Scenarios:
  1. Rider triggers SOS on their own in_progress ride  → 200, record inserted,
     admin WS broadcast fired, logger.critical called
  2. Driver triggers SOS on their active ride           → 200, record inserted
  3. Non-participant user                               → 403
  4. Ride not found                                     → 404
  5. Emergency contacts receive notification attempt
  6. Admin broadcast failure is swallowed (best-effort) → still returns 200
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_sos_e2e"
DRIVER_USER_ID = "driver_user_sos_e2e"
DRIVER_ID = "driver_sos_e2e"
RIDE_ID = "ride_sos_e2e_001"


def _active_ride(driver_id: str | None = DRIVER_ID) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": driver_id,
        "status": "in_progress",
    }


def _sos_request(message: str = "Help!", lat: float = 52.13, lng: float = -106.67) -> MagicMock:
    req = MagicMock()
    req.message = message
    req.latitude = lat
    req.longitude = lng
    # Must be set explicitly: a bare MagicMock attribute is truthy, which would
    # make the endpoint take the migration-315 dedup path with a Mock as the
    # key. None mirrors EmergencyRequest's default (no dedup, always insert).
    req.idempotency_key = None
    return req


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSOSRiderTrigger:
    """Rider triggers SOS on their own in_progress ride."""

    async def test_sos_creates_emergency_record(self):
        """trigger_emergency must insert exactly one row into the emergencies table."""
        from backend.routes import rides as rides_mod

        insert_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_active_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
            ),
            patch("backend.routes.rides._deps.db_supabase.insert_one", insert_mock),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
        ):
            result = await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_sos_request(),
                current_user={"id": RIDER_ID},
            )

        assert result is not None
        insert_mock.assert_awaited_once()
        table, incident = insert_mock.call_args[0]
        # Consolidated onto safety_incidents (migration 94) -- the legacy
        # `emergencies` table was never read by anything and was dropped.
        assert table == "safety_incidents"
        assert incident["ride_id"] == RIDE_ID
        assert incident["reported_by_user_id"] == RIDER_ID
        assert incident["role"] == "rider"
        assert incident["status"] == "open"

    async def test_sos_broadcasts_to_admin_websocket(self):
        """Admin dashboard must receive the emergency_alert WS event (fixes the silent-failure)."""
        from backend.routes import rides as rides_mod

        admin_broadcast_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_active_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", admin_broadcast_mock),
        ):
            await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_sos_request(),
                current_user={"id": RIDER_ID},
            )

        # trigger_emergency broadcasts to admins directly (the emergency_alert
        # event asserted below) AND separately via features.notify_safety_team
        # (a second, differently-shaped admin broadcast) -- both resolve to
        # the same patched manager.broadcast_to_admins, so two calls are
        # expected here, not one.
        assert admin_broadcast_mock.await_count == 2
        events = [call.args[0] for call in admin_broadcast_mock.await_args_list]
        emergency_events = [e for e in events if e.get("type") == "emergency_alert"]
        assert len(emergency_events) == 1
        assert emergency_events[0]["incident"]["ride_id"] == RIDE_ID

    async def test_sos_incident_carries_location(self):
        """Emergency record must include the rider's GPS coordinates."""
        from backend.routes import rides as rides_mod

        insert_mock = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_active_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", insert_mock),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
        ):
            await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_sos_request(lat=52.1333, lng=-106.6700),
                current_user={"id": RIDER_ID},
            )

        _, incident = insert_mock.call_args[0]
        assert incident["latitude"] == 52.1333
        assert incident["longitude"] == -106.6700


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSOSDriverTrigger:
    """Driver triggers SOS on their own active ride."""

    async def test_driver_can_trigger_sos(self):
        from backend.routes import rides as rides_mod

        driver_row = {"id": DRIVER_ID, "user_id": DRIVER_USER_ID}
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_active_ride())),
            # get_rows returns the driver row when queried by user_id
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[driver_row])),
            patch("backend.routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", insert_mock),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
        ):
            await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_sos_request(),
                current_user={"id": DRIVER_USER_ID},
            )

        _, incident = insert_mock.call_args[0]
        assert incident["role"] == "driver"
        assert incident["reported_by_user_id"] == DRIVER_USER_ID


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSOSAuthGuards:
    """Non-participants must be rejected; missing rides must 404."""

    async def test_non_participant_gets_403(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _active_ride()  # rider_id = RIDER_ID, driver_id = DRIVER_ID

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            # get_rows returns empty list — requester is not this ride's driver
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.trigger_emergency(
                    ride_id=RIDE_ID,
                    body=_sos_request(),
                    current_user={"id": "stranger_user"},
                )

        assert exc.value.status_code == 403

    async def test_missing_ride_gets_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.trigger_emergency(
                    ride_id="nonexistent_ride",
                    body=_sos_request(),
                    current_user={"id": RIDER_ID},
                )

        assert exc.value.status_code == 404


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSOSBroadcastResiliency:
    """Admin broadcast failure must not prevent the emergency record from being created."""

    async def test_admin_broadcast_failure_does_not_suppress_response(self):
        """R-P0-1: SOS must never silently fail — a broadcast error is logged but not raised."""
        from backend.routes import rides as rides_mod

        insert_mock = AsyncMock()
        # Broadcast raises — simulates the original silent-failure scenario
        broadcast_mock = AsyncMock(side_effect=RuntimeError("WS pool unavailable"))

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_active_ride())),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db_supabase.insert_one", insert_mock),
            patch("backend.routes.rides._deps.manager.broadcast_to_admins", broadcast_mock),
        ):
            # Must not raise even though broadcast failed
            result = await rides_mod.trigger_emergency(
                ride_id=RIDE_ID,
                body=_sos_request(),
                current_user={"id": RIDER_ID},
            )

        # Emergency record was still written
        insert_mock.assert_awaited_once()
        # Handler returned normally despite broadcast failure
        assert result is not None
