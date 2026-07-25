"""Unit tests for the per-insurance-period distance audit writer.

Contract (record_ride_period_distances):
  - writes one append-only row per ride-scoped period (2 en-route, 3 passenger);
  - replay-safe: periods already present for the ride are skipped;
  - best-effort: an insert failure is logged, never raised (settlement safety);
  - PII-safe / billing-safe: distances only, never coordinates or fares.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import period_distance_audit as mod

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


def _phases():
    return [
        {"period": 2, "distance_km": 1.2, "started_at": "t0", "ended_at": "t1"},
        {"period": 3, "distance_km": 6.8, "started_at": "t1", "ended_at": "t2"},
    ]


def test_records_both_ride_scoped_periods():
    get_rows = AsyncMock(return_value=[])
    insert = AsyncMock(return_value={"id": "x"})
    with patch.object(mod.db_supabase, "get_rows", get_rows), patch.object(mod.db_supabase, "insert_one", insert):
        written = _run(mod.record_ride_period_distances(driver_id="d1", ride_id="r1", phases=_phases()))

    assert written == 2
    rows = [c.args[1] for c in insert.await_args_list]
    assert {r["period"] for r in rows} == {2, 3}
    p3 = next(r for r in rows if r["period"] == 3)
    assert p3["distance_km"] == 6.8
    assert p3["driver_id"] == "d1" and p3["ride_id"] == "r1"
    assert p3["source"] == "gps_measured"


def test_replay_safe_skips_existing_periods():
    # Period 2 already recorded → only period 3 is written on a re-run.
    get_rows = AsyncMock(return_value=[{"period": 2}])
    insert = AsyncMock(return_value={"id": "x"})
    with patch.object(mod.db_supabase, "get_rows", get_rows), patch.object(mod.db_supabase, "insert_one", insert):
        written = _run(mod.record_ride_period_distances(driver_id="d1", ride_id="r1", phases=_phases()))

    assert written == 1
    assert insert.await_args.args[1]["period"] == 3


def test_insert_failure_is_swallowed_not_raised():
    get_rows = AsyncMock(return_value=[])
    insert = AsyncMock(side_effect=RuntimeError("db down"))
    with patch.object(mod.db_supabase, "get_rows", get_rows), patch.object(mod.db_supabase, "insert_one", insert):
        # Must not raise — settlement must never fail on the audit side-write.
        written = _run(mod.record_ride_period_distances(driver_id="d1", ride_id="r1", phases=_phases()))
    assert written == 0


def test_invalid_period_or_distance_skipped():
    get_rows = AsyncMock(return_value=[])
    insert = AsyncMock(return_value={"id": "x"})
    phases = [
        {"period": 5, "distance_km": 1.0},  # invalid period
        {"period": 3, "distance_km": None},  # missing distance
        {"period": 2, "distance_km": -1.0},  # negative distance
        {"period": 2, "distance_km": 2.5},  # valid
    ]
    with patch.object(mod.db_supabase, "get_rows", get_rows), patch.object(mod.db_supabase, "insert_one", insert):
        written = _run(mod.record_ride_period_distances(driver_id="d1", ride_id="r1", phases=phases))
    assert written == 1
    assert insert.await_args.args[1] == {
        "driver_id": "d1",
        "ride_id": "r1",
        "period": 2,
        "distance_km": 2.5,
        "started_at": None,
        "ended_at": None,
        "source": "gps_measured",
    }


def test_missing_ids_is_a_noop():
    insert = AsyncMock()
    with patch.object(mod.db_supabase, "insert_one", insert):
        assert _run(mod.record_ride_period_distances(driver_id="", ride_id="r1", phases=_phases())) == 0
    insert.assert_not_awaited()
