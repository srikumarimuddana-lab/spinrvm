"""
P1: Driver-lifecycle endpoints (decline + start)

Pins behavior of two driver-side ride transitions that previously had no
direct E2E coverage:

  * POST /rides/{ride_id}/decline   →  decline_ride        (drivers.py ~1769)
  * POST /rides/{ride_id}/start     →  start_ride          (drivers.py ~1894)

What is pinned
--------------
decline_ride:
  - Happy path: the assigned driver is unset and the ride flips back to
    "searching" so a re-match can pick it up.
  - The handler kicks off match_driver_to_ride(ride_id) as a background task.
  - A 404 is raised when the caller has no driver row (i.e. the user_id does
    not map to any driver).
  - An audit_logs row is written to record the decline (best-effort: the
    handler swallows insert errors).

start_ride:
  - Happy path: driver_arrived → in_progress, with ride_started_at stamped.
  - A "ride_started" WS message is delivered to the rider's channel.
  - 404 when caller has no driver row.
  - No WS event is emitted when the underlying ride lookup returns None
    (defensive guard).

Known gaps (not pinned because the current handlers do not enforce them):
  - decline_ride does NOT verify ownership of the ride before unsetting
    driver_id, nor does it 409 when the ride was already accepted by another
    driver — both are documented here so a future hardening PR can flip the
    expectations and the tests will catch any behavior regression.
  - start_ride does NOT validate the source status (driver_arrived) nor the
    driver-of-record. A future PR adding that guard should extend this file.

Run:
    pytest backend/tests/test_p1_driver_lifecycle.py -v
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


DRIVER_USER_ID = "driver_user_p1_lifecycle"
DRIVER_ID = "driver_row_p1_lifecycle"
OTHER_DRIVER_ID = "driver_row_other"
RIDER_ID = "rider_p1_lifecycle"
RIDE_ID = "ride_p1_lifecycle_001"


def _driver_row(**extra) -> dict:
    """Mirror the helper in test_p1_driver_offline.py for consistency."""
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Driver P1-Lifecycle",
        "rating": 4.9,
        "total_rides": 120,
        "is_online": True,
        "is_available": True,
        "status": "active",
        "is_verified": True,
        **extra,
    }


def _ride(status: str, driver_id: str | None = DRIVER_ID, **extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": driver_id,
        "status": status,
        "pickup_address": "123 Main",
        "dropoff_address": "456 Broadway",
        "pickup_lat": 52.13, "pickup_lng": -106.67,
        "dropoff_lat": 52.12, "dropoff_lng": -106.65,
        "created_at": datetime.utcnow().isoformat(),
        **extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /rides/{ride_id}/decline — driver declines a ride offer
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestDeclineRide:
    """Pins decline_ride: ride is unassigned and re-dispatched.

    Code under test: backend/routes/drivers.py::decline_ride (~line 1769).
    """

    async def test_happy_path_unassigns_and_flips_to_searching(self):
        """Declining clears driver_id and resets status to 'searching'."""
        from backend.routes import drivers as drv_mod

        update_calls: list[tuple[str, dict]] = []

        async def _capture_update(ride_id, updates):
            update_calls.append((ride_id, updates))
            return {}

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch(
                "backend.routes.drivers.db_supabase.update_ride",
                AsyncMock(side_effect=_capture_update),
            ),
            patch("backend.routes.drivers.db.insert_one", AsyncMock()),
            # match_driver_to_ride is imported inside the handler — stub the
            # rides module's binding so the asyncio.create_task() does not
            # hit a real DB.
            patch("backend.routes.rides.match_driver_to_ride", AsyncMock()),
        ):
            result = await drv_mod.decline_ride(
                ride_id=RIDE_ID,
                current_user={"id": DRIVER_USER_ID},
            )

        assert result == {"success": True}
        assert len(update_calls) == 1
        ride_id_arg, updates_arg = update_calls[0]
        assert ride_id_arg == RIDE_ID
        # The whole point of decline: free the ride for re-match.
        assert updates_arg["driver_id"] is None
        assert updates_arg["status"] == "searching"
        assert "updated_at" in updates_arg

    async def test_decline_triggers_rematch_background_task(self):
        """After declining, match_driver_to_ride must be scheduled so the
        rider isn't stranded with a 'searching' ride and no dispatcher."""
        from backend.routes import drivers as drv_mod

        rematch_mock = AsyncMock()

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.drivers.db.insert_one", AsyncMock()),
            patch("backend.routes.rides.match_driver_to_ride", rematch_mock),
        ):
            await drv_mod.decline_ride(
                ride_id=RIDE_ID,
                current_user={"id": DRIVER_USER_ID},
            )
            # match_driver_to_ride is wrapped in asyncio.create_task() — yield
            # to the event loop so the task gets a chance to run before we
            # assert on the mock.
            import asyncio
            await asyncio.sleep(0)

        rematch_mock.assert_awaited_once_with(RIDE_ID)

    async def test_decline_records_audit_log_entry(self):
        """A row in audit_logs lets daily stats count declines per driver."""
        from backend.routes import drivers as drv_mod

        audit_inserts: list[tuple[str, dict]] = []

        async def _capture_insert(table, doc):
            audit_inserts.append((table, doc))
            return {}

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.drivers.db.insert_one", AsyncMock(side_effect=_capture_insert)),
            patch("backend.routes.rides.match_driver_to_ride", AsyncMock()),
        ):
            await drv_mod.decline_ride(
                ride_id=RIDE_ID,
                current_user={"id": DRIVER_USER_ID},
            )

        assert audit_inserts, "decline must emit an audit_logs row"
        table, doc = audit_inserts[0]
        assert table == "audit_logs"
        assert doc["action"] == "ride_declined"
        assert doc["entity_type"] == "ride"
        assert doc["entity_id"] == RIDE_ID
        # The handler reuses the user_email column to store driver_id — pin
        # that contract so the analytics query that depends on it doesn't
        # silently break.
        assert doc["user_email"] == DRIVER_ID

    async def test_decline_returns_404_when_caller_is_not_a_driver(self):
        """A user with no driver row (rider, deleted driver) must get 404
        instead of silently mutating the ride."""
        from backend.routes import drivers as drv_mod
        from fastapi import HTTPException

        update_mock = AsyncMock()

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.drivers.db_supabase.update_ride", update_mock),
            patch("backend.routes.drivers.db.insert_one", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await drv_mod.decline_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": "rider-with-no-driver-row"},
                )

        assert exc_info.value.status_code == 404
        assert "driver" in exc_info.value.detail.lower()
        # And critically: no ride mutation happened.
        update_mock.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# POST /rides/{ride_id}/start — driver starts the trip (no-OTP variant)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestStartRide:
    """Pins start_ride: status flips to in_progress, rider notified.

    Code under test: backend/routes/drivers.py::start_ride (~line 1894).
    Note: this is the no-OTP variant (the OTP-gated path is verify_pickup_otp
    at ~1860).
    """

    async def test_happy_path_flips_status_to_in_progress(self):
        """Start writes status=in_progress and stamps ride_started_at."""
        from backend.routes import drivers as drv_mod

        update_calls: list[tuple[str, dict]] = []

        async def _capture_update(ride_id, updates):
            update_calls.append((ride_id, updates))
            return {}

        ride_after = _ride(status="in_progress")

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch(
                "backend.routes.drivers.db_supabase.update_ride",
                AsyncMock(side_effect=_capture_update),
            ),
            patch(
                "backend.routes.drivers.db_supabase.get_ride",
                AsyncMock(return_value=ride_after),
            ),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers.send_push_notification", AsyncMock()),
        ):
            result = await drv_mod.start_ride(
                ride_id=RIDE_ID,
                current_user={"id": DRIVER_USER_ID},
            )

        assert result == {"success": True}
        assert len(update_calls) == 1
        ride_id_arg, updates_arg = update_calls[0]
        assert ride_id_arg == RIDE_ID
        assert updates_arg["status"] == "in_progress"
        assert "ride_started_at" in updates_arg
        assert "updated_at" in updates_arg

    async def test_start_emits_ride_started_ws_event_to_rider(self):
        """The rider's app must receive a 'ride_started' event so the UI
        switches from 'driver arrived' to the in-trip view immediately."""
        from backend.routes import drivers as drv_mod

        ws_calls: list[tuple[dict, str]] = []

        async def _capture_ws(message, channel):
            ws_calls.append((message, channel))

        ride_after = _ride(status="in_progress")

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock()),
            patch(
                "backend.routes.drivers.db_supabase.get_ride",
                AsyncMock(return_value=ride_after),
            ),
            patch(
                "backend.routes.drivers.manager.send_personal_message",
                AsyncMock(side_effect=_capture_ws),
            ),
            patch("backend.routes.drivers.send_push_notification", AsyncMock()),
        ):
            await drv_mod.start_ride(
                ride_id=RIDE_ID,
                current_user={"id": DRIVER_USER_ID},
            )

        rider_msgs = [
            msg for msg, ch in ws_calls if ch == f"rider_{RIDER_ID}"
        ]
        assert rider_msgs, "rider must receive a WS event when ride starts"
        assert rider_msgs[0]["type"] == "ride_started"
        assert rider_msgs[0]["ride_id"] == RIDE_ID

    async def test_start_returns_404_when_caller_is_not_a_driver(self):
        """Like decline, start must reject non-driver callers before mutating
        any ride state."""
        from backend.routes import drivers as drv_mod
        from fastapi import HTTPException

        update_mock = AsyncMock()

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.drivers.db_supabase.update_ride", update_mock),
            patch("backend.routes.drivers.db_supabase.get_ride", AsyncMock(return_value=None)),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await drv_mod.start_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": "rider-with-no-driver-row"},
                )

        assert exc_info.value.status_code == 404
        assert "driver" in exc_info.value.detail.lower()
        update_mock.assert_not_awaited()

    async def test_start_skips_ws_when_ride_lookup_returns_none(self):
        """If get_ride returns None (race / deleted ride), the handler must
        not try to read rider_id off of None — it should silently skip the
        notify step and still return success=True."""
        from backend.routes import drivers as drv_mod

        ws_mock = AsyncMock()
        push_mock = AsyncMock()

        with (
            patch(
                "backend.routes.drivers.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock()),
            patch(
                "backend.routes.drivers.db_supabase.get_ride",
                AsyncMock(return_value=None),
            ),
            patch("backend.routes.drivers.manager.send_personal_message", ws_mock),
            patch("backend.routes.drivers.send_push_notification", push_mock),
        ):
            result = await drv_mod.start_ride(
                ride_id=RIDE_ID,
                current_user={"id": DRIVER_USER_ID},
            )

        assert result == {"success": True}
        ws_mock.assert_not_awaited()
        push_mock.assert_not_awaited()
