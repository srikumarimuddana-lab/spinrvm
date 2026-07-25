"""Unit tests for the Period-1 deadhead-distance finalizer loop."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import period1_distance_finalizer as mod

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


def _fake_db(*, claim_result=1):
    return SimpleNamespace(
        supabase=object(),  # non-None so _finalize_one proceeds
        run_sync=AsyncMock(return_value=claim_result),
        insert_one=AsyncMock(return_value={"id": "a"}),
    )


# --- _driver_left_period1 --------------------------------------------------


def test_left_period1_when_offline():
    assert _run(mod._driver_left_period1({"id": "d", "is_online": False})) is True


def test_left_period1_when_on_ride():
    with patch.object(mod, "resolve_active_ride", AsyncMock(return_value={"id": "r"})):
        assert _run(mod._driver_left_period1({"id": "d", "is_online": True})) is True


def test_still_in_period1_when_online_no_ride():
    with patch.object(mod, "resolve_active_ride", AsyncMock(return_value=None)):
        assert _run(mod._driver_left_period1({"id": "d", "is_online": True})) is False


# --- _finalize_one ---------------------------------------------------------


def test_finalize_claims_then_writes_period1_row():
    db = _fake_db(claim_result=1)
    row = {"id": "d1", "period1_accum_km": 3.4, "period1_accum_since": "t0"}
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one(row, "t9")) is True
    written = db.insert_one.await_args.args
    assert written[0] == "driver_period_distances"
    assert written[1] == {
        "driver_id": "d1",
        "ride_id": None,
        "period": 1,
        "distance_km": 3.4,
        "started_at": "t0",
        "ended_at": "t9",
        "source": "gps_scalar_p1",
    }


def test_finalize_skips_when_claim_lost():
    db = _fake_db(claim_result=0)  # another replica/tick already claimed it
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one({"id": "d1", "period1_accum_km": 3.4}, "t9")) is False
    db.insert_one.assert_not_awaited()


def test_finalize_noop_on_zero_accum():
    db = _fake_db()
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one({"id": "d1", "period1_accum_km": 0}, "t9")) is False
    db.insert_one.assert_not_awaited()


# --- _tick -----------------------------------------------------------------


def test_tick_noop_when_flag_off():
    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value={"period1_distance_tracking_enabled": False})),
        patch.object(mod, "_pending_accumulators", AsyncMock()) as pending,
    ):
        assert _run(mod._tick()) == 0
    pending.assert_not_awaited()


def test_tick_finalizes_only_drivers_who_left_period1():
    rows = [
        {"id": "off", "is_online": False, "period1_accum_km": 1.0},  # offline → finalize
        {"id": "still", "is_online": True, "period1_accum_km": 2.0},  # online no ride → skip
    ]
    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value={"period1_distance_tracking_enabled": True})),
        patch.object(mod, "_pending_accumulators", AsyncMock(return_value=rows)),
        patch.object(mod, "resolve_active_ride", AsyncMock(return_value=None)),
        patch.object(mod, "_finalize_one", AsyncMock(return_value=True)) as fin,
    ):
        recorded = _run(mod._tick())
    assert recorded == 1
    assert fin.await_count == 1
    assert fin.await_args.args[0]["id"] == "off"
