"""Idle (Period-1) v2 location batch — persistence + route-layer contract.

Contract:
  - rows land with ride_id NULL / tracking_phase 'online_idle';
  - mocked fixes are rejected terminally (mocked_fix) — strict from day one;
  - points at/after an active ride's window reject as 'ride_active' (the trip
    recorder owns them), earlier points in the same batch still persist;
  - idempotent replay via ON CONFLICT (driver, session, sequence);
  - route layer 409s (client-terminal) ONLY for flag-off or driver-offline;
  - the Period-1 accumulator is fed from accepted rows when the flag is on,
    and the v1 legacy path skips v2-shaped batches (single writer).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from backend.utils import breadcrumbs as bc

pytestmark = pytest.mark.unit

SESSION = "3f0e8f9a-8f2b-4a3f-9c60-1234567890ab"


def _run(coro):
    return asyncio.run(coro)


def _pt(seq, *, minutes_ago=5.0, mocked=False, lat=52.13, lng=-106.67):
    captured = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "sequence_number": seq,
        "captured_at": captured.isoformat(),
        "lat": lat + seq * 0.0001,
        "lng": lng,
        "accuracy": 6,
        "speed": 8,
        "heading": 90,
        "altitude": None,
        "monotonic_ms": 1000 + seq,
        "source": "background",
        "mocked": mocked,
    }


def test_idle_rows_persist_with_null_ride_and_idle_phase(monkeypatch):
    insert = AsyncMock(side_effect=lambda _t, rows, **_k: rows)
    monkeypatch.setattr(bc.db_supabase, "insert_many_ignore_conflicts", insert)

    result, rows = _run(bc.persist_idle_location_batch("drv-1", SESSION, [_pt(0), _pt(1)]))

    assert result.ack.acked_through == 1
    assert result.ack.accepted_count == 2
    assert insert.await_args.kwargs["on_conflict"] == "driver_id,recording_session_id,sequence_number"
    for row in rows:
        assert row["ride_id"] is None
        assert row["tracking_phase"] == "online_idle"
        assert row["driver_id"] == "drv-1"


def test_mocked_fix_rejected_terminally(monkeypatch):
    monkeypatch.setattr(bc.db_supabase, "insert_many_ignore_conflicts", AsyncMock(side_effect=lambda _t, r, **_k: r))

    result, rows = _run(bc.persist_idle_location_batch("drv-1", SESSION, [_pt(0, mocked=True), _pt(1)]))

    assert [r.reason for r in result.ack.rejected] == ["mocked_fix"]
    assert len(rows) == 1
    # acked_through covers the whole submitted range (terminal rejections drain).
    assert result.ack.acked_through == 1


def test_points_inside_active_ride_window_reject_as_ride_active(monkeypatch):
    monkeypatch.setattr(bc.db_supabase, "insert_many_ignore_conflicts", AsyncMock(side_effect=lambda _t, r, **_k: r))
    accepted_at = datetime.now(timezone.utc) - timedelta(minutes=4)
    active_ride = {"id": "ride-9", "driver_accepted_at": accepted_at.isoformat()}

    result, rows = _run(
        bc.persist_idle_location_batch(
            "drv-1",
            SESSION,
            [_pt(0, minutes_ago=10), _pt(1, minutes_ago=2)],  # 1 is inside the ride window
            active_ride=active_ride,
        )
    )

    assert [r.reason for r in result.ack.rejected] == ["ride_active"]
    assert [r["sequence_number"] for r in rows] == [0]


def test_insert_conflict_fallback_plain_insert(monkeypatch):
    monkeypatch.setattr(bc.db_supabase, "insert_many_ignore_conflicts", AsyncMock(side_effect=RuntimeError("no index")))
    plain = AsyncMock(side_effect=lambda _t, rows: rows)
    monkeypatch.setattr(bc.db_supabase, "insert_many", plain)

    result, _rows = _run(bc.persist_idle_location_batch("drv-1", SESSION, [_pt(0)]))

    plain.assert_awaited_once()
    assert result.inserted_count == 1


# ── Route layer ───────────────────────────────────────────────────────────────

from backend.routes.drivers import location as loc  # noqa: E402


def _idle_body(points=None):
    return {
        "session_kind": "online_idle",
        "recording_session_id": SESSION,
        "points": points or [_pt(0), _pt(1)],
    }


def test_parse_routes_idle_bodies_to_idle_model():
    parsed = loc._parse_v2_location_batch(_idle_body())
    assert isinstance(parsed, loc.IdleLocationBatchRequest)
    # ride-less bodies without the explicit kind also parse as idle
    body = _idle_body()
    body.pop("session_kind")
    assert isinstance(loc._parse_v2_location_batch(body), loc.IdleLocationBatchRequest)
    # trip bodies still parse as trip requests
    trip = {"ride_id": "ride-1", "recording_session_id": SESSION, "points": [_pt(0)]}
    assert isinstance(loc._parse_v2_location_batch(trip), loc.LocationBatchRequest)


def _wire_route(monkeypatch, *, flag=True, online=True, active_ride=None):
    driver = {"id": "drv-1", "user_id": "u-1", "is_online": online, "period1_accum_km": 1.0}
    monkeypatch.setattr(loc.db_supabase, "get_rows", AsyncMock(return_value=[driver]))
    update_one = AsyncMock(return_value=driver)
    monkeypatch.setattr(loc.db_supabase, "update_one", update_one)
    monkeypatch.setattr(loc._deps, "mark_present", AsyncMock())
    import backend.settings_loader as sl

    monkeypatch.setattr(
        sl,
        "get_app_settings",
        AsyncMock(return_value={"idle_location_v2_enabled": flag, "period1_distance_tracking_enabled": True}),
    )
    monkeypatch.setattr(bc, "resolve_active_ride", AsyncMock(return_value=active_ride))
    monkeypatch.setattr(bc.db_supabase, "insert_many_ignore_conflicts", AsyncMock(side_effect=lambda _t, r, **_k: r))
    return update_one


@pytest.mark.anyio
async def test_route_persists_and_feeds_period1_accumulator(monkeypatch):
    update_one = _wire_route(monkeypatch)
    request = loc.IdleLocationBatchRequest.model_validate(_idle_body())

    ack = await loc._persist_v2_idle_batch(request, {"id": "u-1"})

    assert ack["acked_through"] == 1
    fields = update_one.await_args.args[2]
    assert "lat" in fields and "lng" in fields
    assert fields["period1_accum_km"] > 1.0  # accumulated on top of the existing 1.0


@pytest.mark.anyio
@pytest.mark.parametrize("flag,online", [(False, True), (True, False)])
async def test_route_409s_terminally_when_flag_off_or_offline(monkeypatch, flag, online):
    _wire_route(monkeypatch, flag=flag, online=online)
    request = loc.IdleLocationBatchRequest.model_validate(_idle_body())

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await loc._persist_v2_idle_batch(request, {"id": "u-1"})
    assert exc.value.status_code == 409
