"""KPI instrumentation for dispatch (CLAUDE.md metric naming scheme).

Pins that the dispatch hot paths emit:
  - spinr_dispatch_offer_sent_total      (match_driver_to_ride, per offer row)
  - spinr_dispatch_offer_accepted_total  (accept_ride, per winning accept)
  - spinr_dispatch_offer_to_accept_duration_ms (accept_ride, from the winner's
    own ride_offers.offered_at — the KPI table's P95 < 2s dispatch latency)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import metrics

RIDE_ID = "ride-metrics-001"
RIDER_ID = "rider-metrics"
DRIVER_ID = "driver-row-metrics"
DRIVER_USER_ID = "driver-user-metrics"


def _counter_total(name: str) -> int:
    return sum(metrics.snapshot()["counters"].get(name, {}).values())


def _histogram_cell(name: str):
    series = metrics.snapshot()["histograms"].get(name, {})
    return series.get(()) if series else None


def _driver() -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "lat": 52.13,
        "lng": -106.67,
        "rating": 4.8,
        "is_wav": False,
        "is_online": True,
        "is_verified": True,
        "status": "active",
        "vehicle_type_id": "economy",
    }


def _ride(status: str = "searching", driver_id=None) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "vehicle_type_id": "economy",
        "requires_wav": False,
        "status": status,
        "driver_id": driver_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.anyio
async def test_match_driver_to_ride_counts_offers_sent():
    from backend.routes import rides as rides_mod

    before = _counter_total("spinr_dispatch_offer_sent_total")

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch(
            "backend.routes.rides._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 4.0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
        patch("backend.routes.rides._deps.db_supabase.claim_driver_atomic", AsyncMock(return_value=True)),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
        patch(
            "backend.routes.rides._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
        ),
        # ride_offers insert + incentives lookup both go through run_sync.
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=SimpleNamespace(data=[])),
        ),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"offer_timeout_seconds": 15})),
        patch("backend.routes.rides._deps.asyncio.create_task", MagicMock()),
    ):
        try:
            await rides_mod.match_driver_to_ride(ride_id=RIDE_ID)
        except Exception:
            pass  # post-offer enrichment may raise under mocks; offers were sent

    assert _counter_total("spinr_dispatch_offer_sent_total") == before + 1


@pytest.mark.anyio
async def test_find_candidate_drivers_counts_presence_filter_failure():
    """A Redis-presence failure falls back to DB-online drivers AND increments
    spinr_dispatch_presence_filter_failed_total so the degradation is visible."""
    from backend.services.dispatch_service import DispatchService

    before = _counter_total("spinr_dispatch_presence_filter_failed_total")

    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[_driver()])
    svc = DispatchService(db)

    with patch(
        "backend.services.dispatch_service.present_driver_ids",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        rows = await svc.find_candidate_drivers(_ride())

    assert rows == [_driver()]  # safety valve: fall back to DB-online drivers
    assert _counter_total("spinr_dispatch_presence_filter_failed_total") == before + 1


@pytest.mark.anyio
async def test_accept_ride_counts_accept_and_observes_latency():
    from backend.routes import drivers as drivers_mod

    offered_at = datetime.now(timezone.utc) - timedelta(seconds=3)
    pre_ride = _ride("driver_assigned", driver_id=DRIVER_ID)
    post_ride = _ride("driver_accepted", driver_id=DRIVER_ID)

    # First run_sync call = winner ride_offers update (returns the offer row,
    # which carries offered_at); later calls (losers select etc.) return empty.
    run_sync_results = [
        SimpleNamespace(data=[{"driver_id": DRIVER_ID, "offered_at": offered_at.isoformat()}]),
    ]

    async def _run_sync(fn, *a, **kw):
        if run_sync_results:
            return run_sync_results.pop(0)
        return SimpleNamespace(data=[])

    counter_before = _counter_total("spinr_dispatch_offer_accepted_total")
    cell_before = _histogram_cell("spinr_dispatch_offer_to_accept_duration_ms")
    count_before = cell_before["count"] if cell_before else 0

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=pre_ride)),
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=post_ride)),
        patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=_run_sync)),
        # record_period_transition (Period 2 open on acceptance) makes its
        # own db_supabase.run_sync call for the insurance-period RPC --
        # unmocked, it consumes run_sync_results[0] before the winner-offer
        # run_sync call below reaches it, so offered_at parses to None and
        # the latency histogram observation is silently skipped.
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
    ):
        await drivers_mod.accept_ride(ride_id=RIDE_ID, current_user={"id": DRIVER_USER_ID})

    assert _counter_total("spinr_dispatch_offer_accepted_total") == counter_before + 1

    cell = _histogram_cell("spinr_dispatch_offer_to_accept_duration_ms")
    assert cell is not None and cell["count"] == count_before + 1
    # The sample must be the offered_at → now delta (~3s), not a wall-clock zero.
    last_sample_ms = cell["sum"] - (cell_before["sum"] if cell_before else 0.0)
    assert 2000.0 <= last_sample_ms <= 30000.0
