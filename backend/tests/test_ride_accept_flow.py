"""Regression tests for the driver-accept → rider-sees-update handoff.

User report
-----------
    "driver got the ride and accepted but still the rider is still
     searching for driver page"

The rider app polls GET /rides/{id} every 15 s and also listens on
WebSocket for a `driver_accepted` message. Either signal should flip
the store from status=`searching` → `driver_accepted`, which makes the
UI render the driver-info panel instead of the spinning search circle.

Those paths only work if:
  1. POST /drivers/rides/{id}/accept actually flips the DB row to
     status=`driver_accepted` (and the handler's own verify re-read
     agrees with the write).
  2. The follow-up GET /rides/{id} returns that updated status + the
     embedded driver dict that the rider UI needs.
  3. A ws message `{type: "driver_accepted"}` is published to
     `rider_<rider_id>` so the app transitions instantly instead of
     waiting 15 s for the next poll.

These tests exercise those three invariants end-to-end at the handler
level, with DB + WS + push mocked. A regression in any of the three
will break the rider's "Finding driver..." transition in prod.

Also covers the admin force-cancel endpoint added for the live
monitoring page (admins previously had a Cancel button that did
nothing — just removed the pin from the map without calling the API).
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

_MOCK_REQUEST = StarletteRequest(
    {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "root_path": ""}
)

RIDER_ID = "rider-abc"
DRIVER_USER_ID = "driver-user-xyz"
DRIVER_ID = "driver-row-123"
RIDE_ID = "ride-456"


def _ride_row(status: str, driver_id=None, driver_accepted_at=None):
    """Canonical rides-table row shape used across the mocks."""
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": status,
        "driver_id": driver_id,
        "driver_accepted_at": driver_accepted_at,
        "pickup_lat": 50.41,
        "pickup_lng": -104.65,
        "dropoff_lat": 50.45,
        "dropoff_lng": -104.60,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return row


def _driver_row():
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Test Driver",
        "rating": 4.9,
        "total_rides": 120,
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_color": "Blue",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
        "lat": 50.41,
        "lng": -104.65,
        # is_online=True: this fixture is the legitimately-online driver on
        # every accept-flow test in this file; accept_ride now rejects
        # offline drivers (2026-08-18 fleet audit ranked blocker #4).
        "is_online": True,
    }


class TestAcceptRideFlipsStatus:
    """Backend invariant: POST /drivers/rides/{id}/accept must leave the
    ride row in status=`driver_accepted` and emit the WS message the
    rider-app hook is listening for. Without either, the rider screen
    is stuck on the 'Finding your driver' pulse animation."""

    def test_accept_updates_status_and_sends_ws(self):
        """accept_ride uses the atomic db.update_one guard (NOT db_supabase.update_ride)
        and returns {"success": True}, then sends WS + push to notify the rider."""
        from backend.routes import drivers as drivers_mod

        # Ride starts as `driver_assigned` (offer sent to this driver).
        pre_ride = _ride_row("driver_assigned", driver_id=DRIVER_ID)
        # After update_one runs, the re-read via find_one returns the updated row.
        post_ride = _ride_row(
            "driver_accepted",
            driver_id=DRIVER_ID,
            driver_accepted_at=datetime.now(timezone.utc).isoformat(),
        )

        # guard_ok signals that the atomic update matched one row
        guard_ok = type("_Guard", (), {"modified_count": 1})()

        get_ride_mock = AsyncMock(return_value=pre_ride)
        update_one_mock = AsyncMock(return_value=guard_ok)
        get_rows_mock = AsyncMock(return_value=[_driver_row()])
        find_one_mock = AsyncMock(return_value=post_ride)
        send_ws_mock = AsyncMock()
        send_push_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_ride", get_ride_mock),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", get_rows_mock),
            # accept_ride calls db.update_one (db is aliased to db_supabase)
            patch("backend.routes.drivers._deps.db.update_one", update_one_mock),
            patch("backend.routes.drivers._deps.db.find_one", find_one_mock),
            patch("backend.routes.drivers._deps.manager.send_personal_message", send_ws_mock),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", send_push_mock),
        ):
            result = asyncio.run(
                drivers_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )
            )

        assert result == {"success": True}

        # 1) The atomic update_one was called (this is the real DB write path).
        # The handler may issue further update_one calls afterwards (e.g. the
        # ride_metrics pickup-leg write) — the claim is always the FIRST.
        assert update_one_mock.await_count >= 1
        args = update_one_mock.call_args_list[0].args
        # args = (table, filter, patch_doc)
        assert args[0] == "rides"
        patch_payload = args[2].get("$set", args[2])
        assert patch_payload["status"] == "driver_accepted"
        assert patch_payload["driver_id"] == DRIVER_ID
        assert "driver_accepted_at" in patch_payload

        # 2) WS notification fired on the rider_<id> channel — this is
        #    what the rider-app's useRiderSocket.ts listens for to
        #    trigger fetchRide without waiting for the 15 s poll.
        assert send_ws_mock.await_count >= 1
        ws_calls = send_ws_mock.call_args_list
        rider_calls = [c for c in ws_calls if f"rider_{RIDER_ID}" in str(c.args[1])]
        assert rider_calls, (
            f"WS channel must include rider_{RIDER_ID} to match the hook's "
            f"server-side key; got channels: {[c.args[1] for c in ws_calls]}"
        )
        ws_message = rider_calls[0].args[0]
        assert ws_message["type"] == "driver_accepted"
        assert ws_message["ride_id"] == RIDE_ID

        # 3) Push notification too so a backgrounded rider app still gets it.
        assert send_push_mock.await_count >= 1

    def test_accept_records_insurance_period_2(self):
        """Accepting a ride must open insurance Period 2 (en route to pickup —
        TNC primary commercial coverage) for the winning driver. Misclassifying
        this window as Period 1 (contingent liability) is a regulatory/insurance
        liability — a pickup-drive collision would be filed under the wrong layer.
        """
        from backend.routes import drivers as drivers_mod

        pre_ride = _ride_row("driver_assigned", driver_id=DRIVER_ID)
        post_ride = _ride_row(
            "driver_accepted",
            driver_id=DRIVER_ID,
            driver_accepted_at=datetime.now(timezone.utc).isoformat(),
        )
        guard_ok = type("_Guard", (), {"modified_count": 1})()
        period_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=pre_ride)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=guard_ok)),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=post_ride)),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", period_mock),
        ):
            asyncio.run(
                drivers_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )
            )

        # The winning driver must transition to Period 2, linked to this ride.
        period_2_calls = [
            c for c in period_mock.call_args_list if len(c.args) >= 2 and c.args[0] == DRIVER_ID and c.args[1] == 2
        ]
        assert period_2_calls, (
            "accept_ride must record insurance Period 2 for the winning driver; "
            f"got calls: {period_mock.call_args_list}"
        )
        assert period_2_calls[0].kwargs.get("ride_id") == RIDE_ID

    def test_searching_path_claim_filter_requires_unclaimed_ride(self):
        """Broadcast/searching accept must claim with an UNCONDITIONAL atomic
        filter: status=searching AND driver_id IS NULL. Without the driver_id
        clause, the offer-expiry/revert-to-searching window lets two drivers
        both end up accepted (the read of ride.driver_id is non-atomic)."""
        from backend.routes import drivers as drivers_mod

        # Ride is in searching with no driver assigned; this driver holds a
        # pending ride_offers row (batch dispatch).
        pre_ride = _ride_row("searching", driver_id=None)
        post_ride = _ride_row(
            "driver_accepted",
            driver_id=DRIVER_ID,
            driver_accepted_at=datetime.now(timezone.utc).isoformat(),
        )

        async def _get_rows(table, *args, **kwargs):
            if table == "drivers":
                return [_driver_row()]
            if table == "ride_offers":
                return [{"id": "offer-1", "ride_id": RIDE_ID, "driver_id": DRIVER_ID, "status": "pending"}]
            return []

        update_one_mock = AsyncMock(return_value=post_ride)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=pre_ride)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", update_one_mock),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=post_ride)),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                drivers_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )
            )

        assert result == {"success": True}
        # The claim is the first update_one; later calls (ride_metrics) don't count.
        assert update_one_mock.await_count >= 1
        accept_filter = update_one_mock.call_args_list[0].args[1]
        assert accept_filter == {"id": RIDE_ID, "status": "searching", "driver_id": None}

    def test_replay_accept_by_owning_driver_is_idempotent_success(self):
        """User report: with a batch offer to 2+ drivers, the ACCEPTING driver
        got the ride but was also told "Ride already accepted by another
        driver". A duplicate accept request (double-tap, Notifee action +
        in-app tap, network retry) from the driver who already owns the ride
        must return success — never 409."""
        from backend.routes import drivers as drivers_mod

        owned_ride = _ride_row(
            "driver_accepted",
            driver_id=DRIVER_ID,
            driver_accepted_at=datetime.now(timezone.utc).isoformat(),
        )
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=owned_ride)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            patch("backend.routes.drivers._deps.db.update_one", update_one_mock),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=owned_ride)),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                drivers_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )
            )

        assert result["success"] is True
        assert result.get("already_accepted") is True
        # A replay must not attempt another claim write (no side effects re-run).
        update_one_mock.assert_not_awaited()

    def test_concurrent_duplicate_accept_same_driver_returns_success(self):
        """Two accept requests from the SAME driver race: the first wins the
        atomic claim, the second sees guard=None. The re-read shows the ride
        now belongs to this driver → success, not 409 'another driver'."""
        from backend.routes import drivers as drivers_mod

        pre_ride = _ride_row("driver_assigned", driver_id=DRIVER_ID)
        post_ride = _ride_row(
            "driver_accepted",
            driver_id=DRIVER_ID,
            driver_accepted_at=datetime.now(timezone.utc).isoformat(),
        )

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=pre_ride)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver_row()])),
            # Claim fails (first duplicate already flipped the row)...
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
            # ...and the re-read shows this driver owns the ride.
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=post_ride)),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                drivers_mod.accept_ride(
                    ride_id=RIDE_ID,
                    current_user={"id": DRIVER_USER_ID},
                )
            )

        assert result["success"] is True
        assert result.get("already_accepted") is True

    def test_double_accept_rejected_by_guard(self):
        """When the atomic guard returns None (ride already taken by concurrent request),
        accept_ride must raise 409 — never return {success: True}."""
        from fastapi import HTTPException

        from backend.routes import drivers as drivers_mod
        from backend.utils.error_handling import SpinrException

        pre_ride = _ride_row("driver_assigned", driver_id=DRIVER_ID)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=pre_ride)),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver_row()]),
            ),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=pre_ride)),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            with pytest.raises((HTTPException, SpinrException)) as excinfo:
                asyncio.run(
                    drivers_mod.accept_ride(
                        ride_id=RIDE_ID,
                        current_user={"id": DRIVER_USER_ID},
                    )
                )
        assert excinfo.value.status_code == 409


class TestGetRideReturnsAcceptedStatus:
    """The rider app's polling fallback calls GET /rides/{id} every 15 s.
    Once accept lands, the very next poll (and every poll after) must
    return status=driver_accepted AND the embedded driver dict — the UI
    needs both to flip out of the searching view."""

    def test_get_ride_after_accept_has_driver_payload(self):
        from backend.routes import rides as rides_mod

        post_ride = _ride_row(
            "driver_accepted",
            driver_id=DRIVER_ID,
            driver_accepted_at=datetime.now(timezone.utc).isoformat(),
        )
        driver = _driver_row()

        with (
            patch(
                "backend.routes.rides._deps.db_supabase.get_ride",
                AsyncMock(return_value=dict(post_ride)),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=[]),  # caller is the rider, not a driver
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value=driver),
            ),
        ):
            result = asyncio.run(
                rides_mod.get_ride(
                    request=_MOCK_REQUEST,
                    ride_id=RIDE_ID,
                    current_user={"id": RIDER_ID, "role": "rider"},
                )
            )

        assert result["status"] == "driver_accepted"
        assert result.get("driver"), (
            "Rider must receive a .driver payload so the useRideStore "
            "fetchRide() action can populate currentDriver and the "
            "ride-status screen can render the driver-info panel."
        )
        assert result["driver"]["id"] == DRIVER_ID
        assert result["driver"]["name"] == "Test Driver"
        # PII scrub sanity: no phone/license etc. leaked to rider.
        assert "phone" not in result["driver"]
        assert "license_number" not in result["driver"]


class TestAdminCancelRide:
    """Admin force-cancel from the live monitoring page. Previously the
    'Cancel Ride' button in ride-panel.tsx just removed the pin from
    the local map — no backend call. Now it hits POST
    /api/admin/rides/{id}/cancel which flips the row, frees the driver,
    and notifies both sides."""

    def test_cancel_flips_status_and_notifies_both_sides(self):
        from backend.routes.admin import rides as admin_rides

        pre_ride = _ride_row("driver_accepted", driver_id=DRIVER_ID)
        post_ride = _ride_row("cancelled", driver_id=DRIVER_ID)

        get_ride_mock = AsyncMock(side_effect=[pre_ride, post_ride])
        update_ride_mock = AsyncMock(return_value=post_ride)
        set_avail_mock = AsyncMock()
        get_driver_mock = AsyncMock(return_value=_driver_row())
        send_ws_mock = AsyncMock()
        send_push_mock = AsyncMock()

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", get_ride_mock),
            patch("backend.routes.admin.rides.db_supabase.update_ride", update_ride_mock),
            patch(
                "backend.routes.admin.rides.db_supabase.set_driver_available",
                set_avail_mock,
            ),
            patch(
                "backend.routes.admin.rides.db_supabase.get_driver_by_id",
                get_driver_mock,
            ),
            patch(
                "backend.routes.admin.rides.manager.send_personal_message",
                send_ws_mock,
            ),
            patch(
                "backend.routes.admin.rides.send_push_notification",
                send_push_mock,
            ),
            patch(
                "backend.routes.admin.rides.log_admin_action",
                AsyncMock(return_value="audit-1"),
            ) as audit_mock,
        ):
            from backend.routes.admin.rides import AdminCancelRideRequest

            result = asyncio.run(
                admin_rides.admin_cancel_ride(
                    ride_id=RIDE_ID,
                    body=AdminCancelRideRequest(reason="ops test"),
                    admin_user={"id": "admin-1", "role": "admin"},
                )
            )

        assert result["success"] is True
        assert result["status"] == "cancelled"
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "ride_cancelled_by_admin"

        # The write must set status=cancelled + stamp the reason + admin id
        update_payload = update_ride_mock.call_args.args[1]
        assert update_payload["status"] == "cancelled"
        assert update_payload["cancellation_reason"] == "ops test"
        assert update_payload["cancelled_by_admin_id"] == "admin-1"

        # Driver freed so they can take new rides immediately.
        set_avail_mock.assert_awaited_once_with(DRIVER_ID, True)

        # Both sides notified: 1 rider_<id> ws + 1 driver_<user_id> ws.
        ws_channels = [call.args[1] for call in send_ws_mock.call_args_list]
        assert f"rider_{RIDER_ID}" in ws_channels
        assert f"driver_{DRIVER_USER_ID}" in ws_channels

    def test_cancel_rejects_terminal_states(self):
        """Admin cannot 'cancel' an already-completed or already-cancelled
        ride — the endpoint should 400 with no DB write."""
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides
        from backend.routes.admin.rides import AdminCancelRideRequest
        from backend.utils.error_handling import SpinrException

        update_ride_mock = AsyncMock()
        with (
            patch(
                "backend.routes.admin.rides.db_supabase.get_ride",
                AsyncMock(return_value=_ride_row("completed", driver_id=DRIVER_ID)),
            ),
            patch("backend.routes.admin.rides.db_supabase.update_ride", update_ride_mock),
        ):
            with pytest.raises((HTTPException, SpinrException)) as excinfo:
                asyncio.run(
                    admin_rides.admin_cancel_ride(
                        ride_id=RIDE_ID,
                        body=AdminCancelRideRequest(),
                        admin_user={"id": "admin-1"},
                    )
                )
        assert excinfo.value.status_code == 400
        update_ride_mock.assert_not_awaited()
