"""Tests for utils/stale_p3_closer.py — stale open Period-3 span detection.

Pins the safety contract:
  - alert-first: detection fires with the flag OFF, but nothing is closed
  - class A (ride_terminal): closes at the ride's own end time, after grace
  - class B (ride_abandoned): BOTH thresholds must hold; closes at the last
    breadcrumb time (SPR-PE7TTB lesson: a live trip can outlast its GPS)
  - the close is conditional on ended_at still NULL (concurrent transition wins)
  - contract violations (no ride_id, missing ride, non-P3-compatible status)
    alert without closing
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _span(ride_id="ride-1", started_hours_ago=14.0, span_id="span-1"):
    return {
        "id": span_id,
        "driver_id": "drv-1",
        "ride_id": ride_id,
        "started_at": _iso(NOW - timedelta(hours=started_hours_ago)),
    }


def _patches(
    *,
    spans,
    ride,
    settings,
    last_capture=None,
    close_result=True,
    orphan_rides=None,
    open_rows_for_driver=None,
):
    """Common patch stack for _tick tests. get_rows branches on filters:
    span listing vs per-driver open-row check (driver_insurance_periods),
    by-id lookup vs the orphan in_progress sweep (rides)."""

    async def _get_rows(table, filters, **kwargs):
        if table == "driver_insurance_periods":
            if "driver_id" in (filters or {}):
                return open_rows_for_driver or []
            return spans
        if table == "rides":
            if "id" in (filters or {}):
                return [ride] if ride else []
            return orphan_rides or []
        raise AssertionError(f"unexpected table {table}")

    return (
        patch("utils.stale_p3_closer.get_app_settings", AsyncMock(return_value=settings)),
        patch("utils.stale_p3_closer.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(
            "utils.stale_p3_closer._latest_capture_time",
            AsyncMock(return_value=last_capture),
        ),
        patch(
            "utils.stale_p3_closer._close_span",
            AsyncMock(return_value=close_result),
        ),
        patch("utils.stale_p3_closer.datetime", MagicMock(now=MagicMock(return_value=NOW))),
    )


async def _run_tick():
    from utils.stale_p3_closer import _tick

    return await _tick()


@pytest.mark.asyncio
async def test_terminal_ride_alerts_but_does_not_close_when_flag_off():
    ride = {
        "id": "ride-1",
        "status": "completed",
        "ride_completed_at": _iso(NOW - timedelta(hours=3)),
    }
    patches = _patches(spans=[_span()], ride=ride, settings={})
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 1, "closed": 0, "orphaned": 0}
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_ride_closes_at_ride_end_time_when_flag_on():
    ended = NOW - timedelta(hours=3)
    ride = {"id": "ride-1", "status": "completed", "ride_completed_at": _iso(ended)}
    patches = _patches(spans=[_span()], ride=ride, settings={"stale_p3_autoclose_enabled": True})
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 1, "closed": 1, "orphaned": 0}
    close_mock.assert_awaited_once()
    _span_arg, end_time, reason = close_mock.await_args.args
    assert end_time == ended
    assert reason == "ride_terminal"


@pytest.mark.asyncio
async def test_terminal_ride_within_grace_is_skipped():
    """Completion 10 min ago — the normal fire-and-forget period write may
    still be racing us. Not stale yet."""
    ride = {
        "id": "ride-1",
        "status": "completed",
        "ride_completed_at": _iso(NOW - timedelta(minutes=10)),
    }
    patches = _patches(spans=[_span()], ride=ride, settings={"stale_p3_autoclose_enabled": True})
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 0}
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_abandoned_ride_closes_at_last_breadcrumb_time():
    last_fix = NOW - timedelta(hours=8)
    ride = {"id": "ride-1", "status": "in_progress"}
    patches = _patches(
        spans=[_span(started_hours_ago=14)],
        ride=ride,
        settings={"stale_p3_autoclose_enabled": True},
        last_capture=last_fix,
    )
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 1, "closed": 1, "orphaned": 0}
    _span_arg, end_time, reason = close_mock.await_args.args
    assert end_time == last_fix
    assert reason == "ride_abandoned"


@pytest.mark.asyncio
async def test_abandoned_ride_with_recent_breadcrumbs_is_left_alone():
    """SPR-PE7TTB lesson: evidence still arriving means the trip may be live.
    Never close a span whose breadcrumbs are fresher than the threshold."""
    ride = {"id": "ride-1", "status": "in_progress"}
    patches = _patches(
        spans=[_span(started_hours_ago=14)],
        ride=ride,
        settings={"stale_p3_autoclose_enabled": True},
        last_capture=NOW - timedelta(hours=1),
    )
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 0}
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_young_in_progress_ride_is_skipped_without_breadcrumb_lookup():
    ride = {"id": "ride-1", "status": "in_progress"}
    patches = _patches(
        spans=[_span(started_hours_ago=2)],
        ride=ride,
        settings={"stale_p3_autoclose_enabled": True},
        last_capture=None,
    )
    with patches[0], patches[1], patches[2] as capture_mock, patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 0}
    capture_mock.assert_not_awaited()
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_contract_violation_statuses_alert_without_closing():
    """Open P3 while the ride says 'searching' — surfaced, never closed."""
    ride = {"id": "ride-1", "status": "searching"}
    patches = _patches(spans=[_span()], ride=ride, settings={"stale_p3_autoclose_enabled": True})
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 0}
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_ride_and_missing_ride_id_never_close():
    patches = _patches(
        spans=[_span(ride_id=None, span_id="s1"), _span(span_id="s2")],
        ride=None,  # ride lookup returns nothing for s2
        settings={"stale_p3_autoclose_enabled": True},
    )
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 0}
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_span_is_conditional_on_open_row():
    """The UPDATE filters ended_at IS NULL — a concurrent transition that
    already closed the row yields 0 rows and the close is NOT counted."""
    from utils.stale_p3_closer import _close_span

    execute_result = MagicMock(data=[])  # 0 rows — someone else closed it
    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.execute.return_value = execute_result
    supa = MagicMock()
    supa.table.return_value = chain

    with (
        patch("utils.stale_p3_closer.db_supabase.supabase", supa),
        patch(
            "utils.stale_p3_closer.db_supabase.run_sync",
            AsyncMock(side_effect=lambda fn, **kw: fn()),
        ),
    ):
        closed = await _close_span(
            {"id": "span-1", "driver_id": "d", "ride_id": "r"},
            NOW,
            "ride_terminal",
        )

    assert closed is False
    chain.is_.assert_called_once_with("ended_at", "null")
    chain.update.assert_called_once_with({"ended_at": NOW.isoformat()})


@pytest.mark.asyncio
async def test_orphaned_in_progress_ride_alerts_every_tick():
    """After a class-B autoclose, the ride stays in_progress with no open
    period row — invisible to the span sweep. The orphan detector must keep
    alerting until an admin resolves the ride. Alert-only, never mutates."""
    patches = _patches(
        spans=[],
        ride=None,
        settings={},
        orphan_rides=[
            {
                "id": "ride-9",
                "driver_id": "drv-9",
                "ride_started_at": _iso(NOW - timedelta(hours=20)),
            }
        ],
        open_rows_for_driver=[],  # no open insurance-period row at all
    )
    with patches[0], patches[1], patches[2], patches[3] as close_mock, patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 1}
    close_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_in_progress_ride_with_an_open_row_is_not_orphaned():
    """A driver with ANY open period row (even Period 0) is visible to the
    normal audit trail — not this detector's business."""
    patches = _patches(
        spans=[],
        ride=None,
        settings={},
        orphan_rides=[
            {
                "id": "ride-9",
                "driver_id": "drv-9",
                "ride_started_at": _iso(NOW - timedelta(hours=20)),
            }
        ],
        open_rows_for_driver=[{"id": "row-1", "period": 3}],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = await _run_tick()

    assert result == {"detected": 0, "closed": 0, "orphaned": 0}
