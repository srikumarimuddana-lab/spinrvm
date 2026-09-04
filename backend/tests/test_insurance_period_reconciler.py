"""Tests for utils/insurance_period_reconciler.py — WS-12 §3 / C55.

Pins the self-healing contract:
  - a driver on a pending offer, or linked to an assigned/accepted/arrived
    ride, or an in_progress ride, with a missing open period row gets one
    opened at the expected period+ride_id (WS-12 §3's acceptance bar: "kill
    the RPC mid-transition -> reconciler restores the open period on next
    tick")
  - an open row at the wrong period (or, for P2/3, the wrong ride_id) is
    corrected the same way
  - a healthy fleet (every open row already matches) is a no-op
  - an online driver with no active ride/offer and no open row gets Period 1
    opened
  - downgrading an online-idle driver's open P2/3 row to P1 only ALERTS
    (never writes) unless insurance_period_reconciler_downgrade_enabled is on
  - offline drivers (not online, no active ride/offer) are never queried or
    touched at all — left to utils/stale_intent_reconciler.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _patches(
    *,
    offers=None,
    assigned_rides=None,
    in_progress_rides=None,
    online_drivers=None,
    open_rows=None,
    settings=None,
):
    """Common patch stack for _tick tests. get_rows branches on table +
    filter shape, mirroring test_stale_p3_closer.py's _patches helper."""
    offers = offers or []
    assigned_rides = assigned_rides or []
    in_progress_rides = in_progress_rides or []
    online_drivers = online_drivers or []
    open_rows = open_rows or []
    settings = settings if settings is not None else {}

    async def _get_rows(table, filters, **kwargs):
        if table == "ride_offers":
            assert filters == {"status": "pending"}
            return offers
        if table == "rides":
            status = (filters or {}).get("status")
            if isinstance(status, dict):
                return assigned_rides
            if status == "in_progress":
                return in_progress_rides
            raise AssertionError(f"unexpected rides filter {filters}")
        if table == "drivers":
            assert filters == {"is_online": True}
            return online_drivers
        if table == "driver_insurance_periods":
            assert "driver_id" in (filters or {}) and "$in" in filters["driver_id"]
            return open_rows
        raise AssertionError(f"unexpected table {table}")

    return (
        patch("utils.insurance_period_reconciler.get_app_settings", AsyncMock(return_value=settings)),
        patch("utils.insurance_period_reconciler.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("utils.insurance_period_reconciler.record_period_transition", AsyncMock()),
    )


async def _run_tick():
    from utils.insurance_period_reconciler import _tick

    return await _tick()


@pytest.mark.asyncio
async def test_missing_period2_open_row_is_healed_from_pending_offer():
    """WS-12 §3 acceptance bar: a dropped Period-2-open write (e.g. the
    original record_period_transition call during dispatch's claim/offer
    loop silently failed) leaves no open row for a driver with a pending
    offer. The reconciler's next tick restores it."""
    patches = _patches(offers=[{"driver_id": "drv-1", "ride_id": "ride-1"}], open_rows=[])
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 1, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-1", 2, ride_id="ride-1")


@pytest.mark.asyncio
async def test_missing_period2_open_row_is_healed_from_assigned_ride():
    patches = _patches(
        assigned_rides=[{"id": "ride-2", "driver_id": "drv-2"}],
        open_rows=[],
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 1, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-2", 2, ride_id="ride-2")


@pytest.mark.asyncio
async def test_missing_period3_open_row_is_healed_from_in_progress_ride():
    patches = _patches(
        in_progress_rides=[{"id": "ride-3", "driver_id": "drv-3"}],
        open_rows=[],
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 1, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-3", 3, ride_id="ride-3")


@pytest.mark.asyncio
async def test_wrong_period_row_is_corrected():
    """Driver's ride went in_progress (expected P3) but the open row is
    still P2 (e.g. the accept-time write raced ahead of the start-time one
    and the start-time RPC call itself failed)."""
    patches = _patches(
        in_progress_rides=[{"id": "ride-4", "driver_id": "drv-4"}],
        open_rows=[{"id": "row-1", "driver_id": "drv-4", "period": 2, "ride_id": "ride-4"}],
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 0, "corrected": 1, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-4", 3, ride_id="ride-4")


@pytest.mark.asyncio
async def test_wrong_ride_id_at_same_period_is_corrected():
    """Open row is period 2 but tied to a stale ride_id — the driver's
    active ride/offer moved on to a new ride without a fresh write."""
    patches = _patches(
        assigned_rides=[{"id": "ride-new", "driver_id": "drv-5"}],
        open_rows=[{"id": "row-1", "driver_id": "drv-5", "period": 2, "ride_id": "ride-old"}],
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 0, "corrected": 1, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-5", 2, ride_id="ride-new")


@pytest.mark.asyncio
async def test_healthy_fleet_is_a_noop():
    patches = _patches(
        offers=[{"driver_id": "drv-1", "ride_id": "ride-1"}],
        in_progress_rides=[{"id": "ride-3", "driver_id": "drv-3"}],
        online_drivers=[{"id": "drv-6"}],
        open_rows=[
            {"id": "r1", "driver_id": "drv-1", "period": 2, "ride_id": "ride-1"},
            {"id": "r3", "driver_id": "drv-3", "period": 3, "ride_id": "ride-3"},
            {"id": "r6", "driver_id": "drv-6", "period": 1, "ride_id": None},
        ],
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 0, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_online_driver_with_no_open_row_gets_period1_opened():
    patches = _patches(online_drivers=[{"id": "drv-7"}], open_rows=[])
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 1, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-7", 1, ride_id=None)


@pytest.mark.asyncio
async def test_online_idle_driver_with_open_period2_only_alerts_when_flag_off():
    """Downgrade candidate: driver appears online with no active ride/offer,
    but still has an open P2 row. Default (flag off) -> alert only, no write —
    protects against the reconciler's own scan having missed a real ride."""
    patches = _patches(
        online_drivers=[{"id": "drv-8"}],
        open_rows=[{"id": "row-1", "driver_id": "drv-8", "period": 2, "ride_id": "ride-x"}],
        settings={},
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 0, "corrected": 0, "downgrade_alerted": 1}
    rpc_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_online_idle_driver_downgrade_corrected_when_flag_on():
    patches = _patches(
        online_drivers=[{"id": "drv-9"}],
        open_rows=[{"id": "row-1", "driver_id": "drv-9", "period": 2, "ride_id": "ride-x"}],
        settings={"insurance_period_reconciler_downgrade_enabled": True},
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 0, "corrected": 1, "downgrade_alerted": 1}
    rpc_mock.assert_awaited_once_with("drv-9", 1, ride_id=None)


@pytest.mark.asyncio
async def test_offline_driver_not_online_and_not_active_is_never_queried_or_touched():
    """A driver who is neither in the online-drivers result nor linked to
    any active ride/offer never appears in either candidate set, so no
    lookup or write is attempted for them — left entirely to
    utils/stale_intent_reconciler.py."""
    patches = _patches(open_rows=[])
    with patches[0], patches[1] as get_rows_mock, patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 0, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_not_awaited()
    # driver_insurance_periods is queried with an empty id list short-circuited
    # before any get_rows call for that table (see _open_rows_for_drivers).
    called_tables = {c.args[0] for c in get_rows_mock.await_args_list}
    assert "driver_insurance_periods" not in called_tables


@pytest.mark.asyncio
async def test_ride_state_takes_priority_over_stale_pending_offer():
    """A driver has a stale 'pending' ride_offers row for a ride that has
    since moved to driver_accepted — the ride-state evidence must win over
    the (now-outdated) offer, both landing on the same ride_id here."""
    patches = _patches(
        offers=[{"driver_id": "drv-10", "ride_id": "ride-10"}],
        assigned_rides=[{"id": "ride-10", "driver_id": "drv-10"}],
        open_rows=[],
    )
    with patches[0], patches[1], patches[2] as rpc_mock:
        result = await _run_tick()

    assert result == {"opened": 1, "corrected": 0, "downgrade_alerted": 0}
    rpc_mock.assert_awaited_once_with("drv-10", 2, ride_id="ride-10")


@pytest.mark.asyncio
async def test_active_driver_heal_failure_is_logged_and_skipped_not_raised():
    """record_period_transition itself never raises for a DB/RPC failure
    (it swallows by design), but the reconciler's per-driver try/except must
    still keep other drivers' healing going if something unexpected does
    raise (e.g. a bug in this loop's own code)."""
    patches = _patches(
        in_progress_rides=[
            {"id": "ride-a", "driver_id": "drv-a"},
            {"id": "ride-b", "driver_id": "drv-b"},
        ],
        open_rows=[],
    )
    with patches[0], patches[1]:
        with patch(
            "utils.insurance_period_reconciler.record_period_transition",
            AsyncMock(side_effect=[RuntimeError("boom"), None]),
        ) as rpc_mock:
            result = await _run_tick()

    assert rpc_mock.await_count == 2
    assert result["opened"] == 1  # only the 2nd (non-raising) heal counted
