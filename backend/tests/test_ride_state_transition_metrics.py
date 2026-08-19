"""KPI instrumentation for post-acceptance ride-state transitions
(CLAUDE.md metric naming scheme; see backend/tests/test_dispatch_metrics.py
for the pre-acceptance dispatch counters this complements).

2026-08-18 fleet audit: no Prometheus metric existed for any ride-state
transition after offer-acceptance (arrival/start/completion/cancellation),
leaving the match-rate and cancellation-rate KPIs (CLAUDE.md) invisible to
any dashboard or alert — visible only via a manual DB query. Fixed by
emitting one counter, `spinr_rides_state_transition_total{to_status=...}`,
at every production-reachable write site that flips `rides.status` to
driver_arrived / in_progress / completed / cancelled — matching the
existing `spinr_payment_settlement_total{outcome=...}` label convention.

Scope of this file: one representative production call site per distinct
`to_status` label value (arrive_at_pickup → driver_arrived, verify_pickup_otp
→ in_progress, drivers/ride_complete.py::complete_ride → completed,
cancellation.py::cancel_ride_rider → cancelled). The other write sites that
share the same label (routes/rides/lifecycle.py's rider_start_ride /
rider_complete_ride, cancellation.py's cancel_scheduled_ride,
routes/drivers/ride_cancel.py's driver cancel_ride / mark_rider_noshow,
routes/rides/matching.py's ride_search_timeout auto-cancel) use the
identical one-line `_metric_inc(...)` call and are exercised by their own
existing test suites (all pass unmodified after this change) — not
independently re-asserted here to keep this file focused, per the Change
Impact Log's stated scope boundary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import metrics

pytestmark = pytest.mark.anyio

_DRIVER_ID = "drv-txn-metrics-1"
_USER_ID = "user-txn-metrics-1"
_RIDE_ID = "ride-txn-metrics-1"
_RIDER_ID = "rider-txn-metrics-1"


def _counter_total(name: str, **label_filter) -> int:
    series = metrics.snapshot()["counters"].get(name, {})
    if not label_filter:
        return sum(series.values())
    wanted = tuple(sorted(label_filter.items()))
    return sum(v for k, v in series.items() if k == wanted)


def _driver(**kw):
    base = {
        "id": _DRIVER_ID,
        "user_id": _USER_ID,
        "lat": 52.1,
        "lng": -106.6,
        "status": "active",
        "is_online": True,
    }
    base.update(kw)
    return base


def _ride(status="driver_assigned", **kw):
    base = {
        "id": _RIDE_ID,
        "status": status,
        "driver_id": _DRIVER_ID,
        "rider_id": _RIDER_ID,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
        "vehicle_type_id": "vt-1",
    }
    base.update(kw)
    return base


def _spawn_close(coro):
    coro.close()


async def test_arrive_at_pickup_counts_driver_arrived():
    from backend.routes.drivers.ride_flow import arrive_at_pickup

    ride = _ride(status="driver_accepted")

    async def fake_get_rows(table, filters=None, **kw):
        return [_driver()] if table == "drivers" else [ride]

    before = _counter_total("spinr_rides_state_transition_total", to_status="driver_arrived")

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
        patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
    ):
        result = await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

    assert result == {"success": True}
    after = _counter_total("spinr_rides_state_transition_total", to_status="driver_arrived")
    assert after == before + 1


async def test_verify_pickup_otp_counts_in_progress():
    from backend.routes.drivers._shared import RideOTPRequest
    from backend.routes.drivers.ride_flow import verify_pickup_otp

    ride = _ride(status="driver_arrived", pickup_otp="1234")

    async def fake_get_rows(table, filters=None, **kw):
        return [_driver()] if table == "drivers" else [ride]

    before = _counter_total("spinr_rides_state_transition_total", to_status="in_progress")

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
        patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
    ):
        result = await verify_pickup_otp(
            ride_id=_RIDE_ID, request=RideOTPRequest(otp="1234"), current_user={"id": _USER_ID}
        )

    assert result == {"success": True}
    after = _counter_total("spinr_rides_state_transition_total", to_status="in_progress")
    assert after == before + 1


async def test_rider_complete_ride_counts_completed():
    from backend.routes.rides import rider_complete_ride

    ride = _ride(status="in_progress", rider_id=_RIDER_ID)
    completed_ride = {**ride, "status": "completed"}

    before = _counter_total("spinr_rides_state_transition_total", to_status="completed")

    with (
        patch("backend.routes.rides._deps.db_supabase") as mock_db,
        patch("backend.routes.rides._deps.record_period_transition", new_callable=AsyncMock),
    ):
        mock_db.get_ride = AsyncMock(side_effect=[ride, completed_ride])
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value=ride)
        mock_db.set_driver_available = AsyncMock()
        mock_db.get_driver_by_id = AsyncMock(return_value={"id": _DRIVER_ID, "user_id": _USER_ID})
        result = await rider_complete_ride(ride_id=_RIDE_ID, current_user={"id": _RIDER_ID})

    assert result["status"] == "completed"
    after = _counter_total("spinr_rides_state_transition_total", to_status="completed")
    assert after == before + 1


async def test_cancel_ride_rider_counts_cancelled():
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching", driver_id=None)
    cancelled = _ride(status="cancelled", driver_id=None)

    before = _counter_total("spinr_rides_state_transition_total", to_status="cancelled")

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        mock_supabase.get_app_settings = AsyncMock(return_value={})
        mock_supabase.update_ride = AsyncMock(return_value=None)
        mock_supabase.get_ride = AsyncMock(return_value=cancelled)
        mock_supabase.set_driver_available = AsyncMock(return_value=None)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.run_sync = AsyncMock(return_value=MagicMock(data=[]))
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()
        mock_manager.broadcast_to_admins = AsyncMock()

        result = await cancel_ride_rider(request=None, ride_id=_RIDE_ID, reason="test", current_user={"id": _RIDER_ID})

    assert result["success"] is True
    after = _counter_total("spinr_rides_state_transition_total", to_status="cancelled")
    assert after == before + 1
