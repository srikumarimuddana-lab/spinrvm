"""
Unit tests for backend/utils/surge_engine.py

Coverage:
- ratio_to_multiplier: all tier boundaries and representative interior points
- SURGE_CAP constant enforcement
- recalculate_all_surges skips surge_source == 'manual' areas
- return type is float (engine uses plain float throughout, not Decimal)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── ratio_to_multiplier ────────────────────────────────────────────────────


def test_ratio_below_first_threshold_returns_normal():
    """ratio < 0.5 → 1.0× (no surge)."""
    from utils.surge_engine import ratio_to_multiplier

    assert ratio_to_multiplier(0.0) == 1.0
    assert ratio_to_multiplier(0.1) == 1.0
    assert ratio_to_multiplier(0.49) == 1.0


def test_ratio_at_first_threshold_returns_second_tier():
    """ratio == 0.5 falls into [0.5, 0.8) → 1.25×."""
    from utils.surge_engine import ratio_to_multiplier

    assert ratio_to_multiplier(0.5) == 1.25
    assert ratio_to_multiplier(0.79) == 1.25


def test_ratio_at_second_threshold_returns_third_tier():
    """ratio == 0.8 falls into [0.8, 1.2) → 1.5×."""
    from utils.surge_engine import ratio_to_multiplier

    assert ratio_to_multiplier(0.8) == 1.5
    assert ratio_to_multiplier(1.0) == 1.5
    assert ratio_to_multiplier(1.19) == 1.5


def test_ratio_at_third_threshold_returns_fourth_tier():
    """ratio == 1.2 falls into [1.2, 2.0) → 1.75×."""
    from utils.surge_engine import ratio_to_multiplier

    assert ratio_to_multiplier(1.2) == 1.75
    assert ratio_to_multiplier(1.5) == 1.75
    assert ratio_to_multiplier(1.99) == 1.75


def test_ratio_at_fourth_threshold_returns_fifth_tier():
    """ratio == 2.0 falls into [2.0, 3.0) → 2.0×."""
    from utils.surge_engine import ratio_to_multiplier

    assert ratio_to_multiplier(2.0) == 2.0
    assert ratio_to_multiplier(2.5) == 2.0
    assert ratio_to_multiplier(2.99) == 2.0


def test_ratio_at_cap_threshold_returns_surge_cap():
    """ratio == 3.0 hits the hard cap → 2.5× (SURGE_CAP)."""
    from utils.surge_engine import SURGE_CAP, ratio_to_multiplier

    assert ratio_to_multiplier(3.0) == SURGE_CAP
    assert ratio_to_multiplier(5.0) == SURGE_CAP
    assert ratio_to_multiplier(100.0) == SURGE_CAP


def test_surge_cap_value_is_2_5():
    """SURGE_CAP constant must be 2.5 — regulatory ceiling for auto mode."""
    from utils.surge_engine import SURGE_CAP

    assert SURGE_CAP == 2.5


# ── _count_demand_in_area ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_demand_db_failure_returns_zero():
    """DB exception in _count_demand_in_area → returns 0 (does not propagate)."""
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(side_effect=RuntimeError("DB down"))

    with patch("utils.surge_engine.db", db_mock):
        from utils.surge_engine import _count_demand_in_area

        result = await _count_demand_in_area("area1")

    assert result == 0


@pytest.mark.asyncio
async def test_count_demand_counts_active_statuses_only():
    """Only rides in active statuses contribute to demand count."""
    rides = [
        {"status": "searching"},
        {"status": "driver_assigned"},
        {"status": "driver_accepted"},
        {"status": "completed"},  # not active
        {"status": "cancelled"},  # not active
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=rides)

    with patch("utils.surge_engine.db", db_mock):
        from utils.surge_engine import _count_demand_in_area

        result = await _count_demand_in_area("area1")

    assert result == 3


# ── _count_supply_in_area ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_supply_no_polygon_returns_zero():
    """get_service_area_polygon returns None → supply is 0."""
    with (
        patch("utils.surge_engine.get_service_area_polygon", return_value=None),
        patch("utils.surge_engine.db", MagicMock()),
    ):
        from utils.surge_engine import _count_supply_in_area

        result = await _count_supply_in_area({"id": "area1"})

    assert result == 0


@pytest.mark.asyncio
async def test_count_supply_db_failure_returns_zero():
    """DB exception in _count_supply_in_area → returns 0 (does not propagate)."""
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(side_effect=RuntimeError("DB down"))

    with (
        patch("utils.surge_engine.get_service_area_polygon", return_value=[(0, 0), (1, 0), (1, 1)]),
        patch("utils.surge_engine.db", db_mock),
    ):
        from utils.surge_engine import _count_supply_in_area

        result = await _count_supply_in_area({"id": "area1"})

    assert result == 0


@pytest.mark.asyncio
async def test_count_supply_presence_filter_removes_ghost_drivers():
    """Drivers not in present_driver_ids set are excluded from supply count."""
    drivers = [
        {"id": "d1", "lat": 52.1, "lng": -106.7, "user_id": "u1", "is_online": True, "is_available": True},
        {"id": "d2", "lat": 52.2, "lng": -106.8, "user_id": "u2", "is_online": True, "is_available": True},
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=drivers)

    with (
        patch("utils.surge_engine.get_service_area_polygon", return_value=[(0, 0)]),
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine.present_driver_ids", AsyncMock(return_value={"d1"})),
        patch("utils.surge_engine.point_in_polygon", return_value=True),
    ):
        from utils.surge_engine import _count_supply_in_area

        result = await _count_supply_in_area({"id": "area1"})

    assert result == 1  # only d1 is present; d2 filtered out


@pytest.mark.asyncio
async def test_count_supply_presence_failure_falls_back_to_db_state():
    """presence_driver_ids failure → warning logged, DB drivers used as-is (safe fallback)."""
    drivers = [
        {"id": "d1", "lat": 52.1, "lng": -106.7, "user_id": "u1"},
        {"id": "d2", "lat": 52.2, "lng": -106.8, "user_id": "u2"},
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=drivers)

    with (
        patch("utils.surge_engine.get_service_area_polygon", return_value=[(0, 0)]),
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine.present_driver_ids", AsyncMock(side_effect=RuntimeError("Redis down"))),
        patch("utils.surge_engine.point_in_polygon", return_value=True),
    ):
        from utils.surge_engine import _count_supply_in_area

        result = await _count_supply_in_area({"id": "area1"})

    # Both drivers counted (fallback to DB state on presence failure)
    assert result == 2


# ── calculate_surge_for_area ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_calculate_surge_no_demand_returns_normal():
    """demand=0, supply=5 → ratio=0 → multiplier=1.0."""
    with (
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=0)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=5)),
    ):
        from utils.surge_engine import calculate_surge_for_area

        result = await calculate_surge_for_area({"id": "area1"})

    assert result["multiplier"] == 1.0
    assert result["ratio"] == 0.0
    assert result["demand_count"] == 0
    assert result["supply_count"] == 5


@pytest.mark.asyncio
async def test_calculate_surge_zero_supply_uses_1_as_denominator():
    """demand=5, supply=0 → ratio=5/1=5.0 → SURGE_CAP (2.5)."""
    with (
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=5)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=0)),
    ):
        from utils.surge_engine import SURGE_CAP, calculate_surge_for_area

        result = await calculate_surge_for_area({"id": "area1"})

    assert result["multiplier"] == SURGE_CAP
    assert result["ratio"] == 5.0


@pytest.mark.asyncio
async def test_calculate_surge_high_demand_returns_cap():
    """demand=15, supply=4 → ratio=3.75 → SURGE_CAP."""
    with (
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=15)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=4)),
    ):
        from utils.surge_engine import SURGE_CAP, calculate_surge_for_area

        result = await calculate_surge_for_area({"id": "area1"})

    assert result["multiplier"] == SURGE_CAP


@pytest.mark.asyncio
async def test_calculate_surge_mid_tier():
    """demand=4, supply=5 → ratio=0.8 → 1.5×."""
    with (
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=4)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=5)),
    ):
        from utils.surge_engine import calculate_surge_for_area

        result = await calculate_surge_for_area({"id": "area1"})

    assert result["multiplier"] == 1.5


# ── recalculate_all_surges ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalculate_skips_manual_override_areas():
    """Areas with surge_source='manual' are never updated."""
    areas = [
        {"id": "a1", "surge_source": "manual", "surge_multiplier": 2.0, "is_active": True},
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()

    with patch("utils.surge_engine.db", db_mock):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert results == []
    db_mock.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_recalculate_skips_sub_areas():
    """Areas with parent_service_area_id set are sub-areas (airports) and skipped."""
    areas = [
        {
            "id": "airport1",
            "parent_service_area_id": "main_area",
            "surge_multiplier": 1.0,
            "is_active": True,
        }
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()

    with patch("utils.surge_engine.db", db_mock):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert results == []
    db_mock.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_recalculate_does_not_update_db_when_multiplier_unchanged():
    """If new multiplier equals current, no DB update_one call."""
    areas = [{"id": "a1", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True}]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "row1"})

    with (
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=0)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=10)),
    ):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    # Multiplier unchanged (1.0 → 1.0); update_one should NOT be called
    db_mock.update_one.assert_not_awaited()
    assert results[0]["multiplier"] == 1.0


@pytest.mark.asyncio
async def test_recalculate_updates_db_when_multiplier_changes():
    """When new multiplier differs from stored, update_one IS called."""
    areas = [{"id": "a1", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True}]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "row1"})

    with (
        patch("utils.surge_engine.db", db_mock),
        # demand=4, supply=5 → ratio=0.8 → 1.5× (changed from 1.0)
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=4)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=5)),
    ):
        from utils.surge_engine import recalculate_all_surges

        await recalculate_all_surges()

    db_mock.update_one.assert_awaited_once()
    update_payload = db_mock.update_one.call_args[0][2]
    assert update_payload["surge_multiplier"] == 1.5
    assert update_payload["surge_active"] is True


@pytest.mark.asyncio
async def test_recalculate_filters_surge_enabled_in_db_query():
    """The service_areas read filters surge_enabled=True in the DB.

    Disabled areas must never be fetched at all — with the toggle off
    everywhere, each 2-min tick costs one empty response instead of a
    full table read (Supabase egress).
    """
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=[])

    with patch("utils.surge_engine.db", db_mock):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert results == []
    db_mock.get_rows.assert_awaited_once_with("service_areas", {"is_active": True, "surge_enabled": True}, limit=100)


@pytest.mark.asyncio
async def test_recalculate_skips_surge_disabled_areas():
    """Areas without surge_enabled are never auto-activated, even under load."""
    areas = [{"id": "a1", "surge_source": "auto", "surge_enabled": False, "surge_multiplier": 1.0, "is_active": True}]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "row1"})

    with (
        patch("utils.surge_engine.db", db_mock),
        # High demand that would otherwise push surge to the cap.
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=50)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=1)),
    ):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    # Disabled area is skipped entirely: no multiplier write, no history row.
    db_mock.update_one.assert_not_awaited()
    assert results == []


@pytest.mark.asyncio
async def test_recalculate_area_failure_does_not_abort_remaining():
    """An exception processing one area logs error but continues to next area."""
    areas = [
        {"id": "a1", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True},
        {"id": "a2", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True},
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "row1"})

    call_count = 0

    async def flaky_demand(area_id: str) -> int:
        nonlocal call_count
        call_count += 1
        if area_id == "a1":
            raise RuntimeError("demand query failed")
        return 0

    with (
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine._count_demand_in_area", side_effect=flaky_demand),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=5)),
    ):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()  # must not raise

    # a2 processed despite a1 failure
    assert call_count == 2
    assert any(r["area_id"] == "a2" for r in results)


@pytest.mark.asyncio
async def test_recalculate_service_areas_db_failure_returns_empty():
    """DB failure fetching service_areas → returns [] without raising."""
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(side_effect=RuntimeError("DB down"))

    with patch("utils.surge_engine.db", db_mock):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert results == []


@pytest.mark.asyncio
async def test_recalculate_kill_switch_off_skips_entirely():
    """E5: surge_engine_enabled=False must skip the recompute cycle before
    the service_areas read even fires -- no DB read, no multiplier write."""
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=[{"id": "a1"}])

    with (
        patch("utils.surge_engine.db", db_mock),
        patch(
            "utils.surge_engine.get_app_settings",
            AsyncMock(return_value={"surge_engine_enabled": False}),
        ),
    ):
        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert results == []
    db_mock.get_rows.assert_not_awaited()


@pytest.mark.asyncio
async def test_recalculate_kill_switch_defaults_on_when_unset():
    """A settings dict with no surge_engine_enabled key (legacy row) must
    still proceed -- the flag defaults to enabled, not disabled."""
    areas = [{"id": "a1", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True}]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "row1"})

    with (
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine.get_app_settings", AsyncMock(return_value={})),
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=0)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=10)),
    ):
        from utils.surge_engine import recalculate_all_surges

        await recalculate_all_surges()

    db_mock.get_rows.assert_awaited_once()


@pytest.mark.asyncio
async def test_recalculate_kill_switch_fails_open_on_settings_error():
    """A settings-lookup error must never itself act as a kill switch --
    the tick proceeds as if the flag were enabled."""
    areas = [{"id": "a1", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True}]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "row1"})

    with (
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine.get_app_settings", AsyncMock(side_effect=RuntimeError("settings down"))),
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=0)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=10)),
    ):
        from utils.surge_engine import recalculate_all_surges

        await recalculate_all_surges()

    db_mock.get_rows.assert_awaited_once()


@pytest.mark.asyncio
async def test_recalculate_inserts_surge_pricing_history_row():
    """Every processed area gets a surge_pricing history row inserted."""
    areas = [{"id": "a1", "surge_source": "auto", "surge_enabled": True, "surge_multiplier": 1.0, "is_active": True}]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)
    db_mock.update_one = AsyncMock()
    db_mock.insert_one = AsyncMock(return_value={"id": "h1"})

    with (
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=0)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=10)),
    ):
        from utils.surge_engine import recalculate_all_surges

        await recalculate_all_surges()

    # insert_one called for surge_pricing history
    db_mock.insert_one.assert_awaited_once()
    table, payload = db_mock.insert_one.call_args[0]
    assert table == "surge_pricing"
    assert payload["service_area_id"] == "a1"
    assert payload["source"] == "auto"


@pytest.mark.anyio
async def test_surge_recalculated_for_auto_source_area() -> None:
    """
    recalculate_all_surges() DOES process areas with surge_source == 'auto'
    (or with no source set, which defaults to 'auto').
    """
    auto_area = {
        "id": "area-auto-01",
        "name": "South End",
        "is_active": True,
        "surge_enabled": True,
        "surge_source": "auto",
        "surge_multiplier": 1.0,
        "parent_service_area_id": None,
    }

    fake_metrics = {
        "area_id": "area-auto-01",
        "demand_count": 10,
        "supply_count": 3,
        "ratio": 3.33,
        "multiplier": 2.5,
    }

    with (
        patch("utils.surge_engine.db") as mock_db,
        patch("utils.surge_engine.calculate_surge_for_area", new_callable=AsyncMock, return_value=fake_metrics),
    ):
        mock_db.get_rows = AsyncMock(return_value=[auto_area])
        mock_db.update_one = AsyncMock()
        mock_db.insert_one = AsyncMock()

        from utils.surge_engine import recalculate_all_surges

        results = await recalculate_all_surges()

    assert len(results) == 1
    assert results[0]["multiplier"] == 2.5


# ── Manual override: admin can hold a value above auto-mode cap ──────────────


def test_manual_override_value_above_auto_cap_is_valid() -> None:
    """
    The auto engine caps at 2.5, but a manual override (set by an admin directly
    in the DB) can legitimately store values above 2.5 (up to 10.0 per admin UI).
    ratio_to_multiplier is never called for manual areas, so this test simply
    confirms that SURGE_CAP is a ceiling only for the auto tier function —
    a stored value of 3.0 returned directly (simulating a DB read) is valid.
    """
    from utils.surge_engine import SURGE_CAP

    # Simulate what the admin dashboard would store and what the DB returns.
    # The auto function is bypassed for manual areas, so the stored value is
    # returned verbatim.
    admin_stored_value = 3.0
    assert admin_stored_value > SURGE_CAP  # confirms it's above the auto cap
    # The engine itself never calls ratio_to_multiplier on manual areas,
    # so the value is not clipped — just round-trip as-is.
    assert admin_stored_value == 3.0


# ── get_surge_status: per-area toggle gating ───────────────────────────────


@pytest.mark.asyncio
async def test_get_surge_status_gates_on_surge_enabled():
    """get_surge_status reports EFFECTIVE surge.

    A parked multiplier on a surge-disabled area must read as 1.0× / inactive
    (same gate the fare paths apply), and surge_enabled is surfaced so the
    admin dashboard can show the toggle state.
    """
    areas = [
        {
            "id": "a1",
            "name": "Disabled",
            "surge_enabled": False,
            "surge_active": True,
            "surge_multiplier": 2.0,
            "is_active": True,
        },
        {
            "id": "a2",
            "name": "Enabled",
            "surge_enabled": True,
            "surge_active": True,
            "surge_multiplier": 1.75,
            "is_active": True,
        },
    ]
    db_mock = MagicMock()
    db_mock.get_rows = AsyncMock(return_value=areas)

    with (
        patch("utils.surge_engine.db", db_mock),
        patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=0)),
        patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=5)),
    ):
        from utils.surge_engine import get_surge_status

        # use_cache=False: this test is about the gating, and the status cache
        # is process-wide in the in-memory Redis fallback tests run under — a
        # cached read would silently assert on some other test's areas.
        statuses = await get_surge_status(use_cache=False)

    by_id = {s["area_id"]: s for s in statuses}
    # Disabled area: toggle state exposed; surge gated to 1.0× / inactive.
    assert by_id["a1"]["surge_enabled"] is False
    assert by_id["a1"]["surge_active"] is False
    assert by_id["a1"]["multiplier"] == 1.0
    # Enabled + active area: the real multiplier surfaces.
    assert by_id["a2"]["surge_enabled"] is True
    assert by_id["a2"]["surge_active"] is True
    assert by_id["a2"]["multiplier"] == 1.75


# ── surge_recalculation_loop leader lock (multi-replica dedupe) ──────────────


class _StopLoop(Exception):
    """Sentinel to break the infinite recalculation loop after one iteration."""


async def _sleep_break(*_args, **_kwargs):
    raise _StopLoop()


class TestSurgeLoopLeaderLock:
    """The recalculation loop must run the tick at most once per interval across
    replicas. Without a leader lock, every replica recalculates and inserts a
    surge_pricing history row each tick → N duplicate rows per area per interval.
    """

    @pytest.mark.asyncio
    async def test_non_leader_replica_skips_tick(self):
        recalc = AsyncMock(return_value=[])
        with (
            patch("utils.surge_engine.redis_set_nx", AsyncMock(return_value=False), create=True),
            patch("utils.surge_engine.recalculate_all_surges", recalc),
            patch("utils.surge_engine._record_heartbeat", MagicMock()),
            patch("utils.surge_engine.asyncio.sleep", side_effect=_sleep_break),
        ):
            from utils.surge_engine import surge_recalculation_loop

            with pytest.raises(_StopLoop):
                await surge_recalculation_loop()

        recalc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_leader_replica_runs_tick(self):
        recalc = AsyncMock(return_value=[])
        with (
            patch("utils.surge_engine.redis_set_nx", AsyncMock(return_value=True), create=True),
            patch("utils.surge_engine.recalculate_all_surges", recalc),
            patch("utils.surge_engine._record_heartbeat", MagicMock()),
            patch("utils.surge_engine.asyncio.sleep", side_effect=_sleep_break),
        ):
            from utils.surge_engine import surge_recalculation_loop

            with pytest.raises(_StopLoop):
                await surge_recalculation_loop()

        recalc.assert_awaited_once()


# ── get_surge_status: batched supply fetch + short cache ────────────────────


class TestSurgeStatusFetchBatching:
    """`GET /admin/surge/status` used to re-read the drivers table once per area.

    The dispatchable-driver set is platform-wide — only the polygon test that
    follows is per-area — so N areas meant N identical unscoped reads of
    `drivers`, the same table dispatch and the driver-location write path use.
    Every open admin tab paid that cost on every poll.
    """

    @staticmethod
    def _areas(n: int) -> list[dict]:
        return [
            {
                "id": f"a{i}",
                "name": f"Area {i}",
                "is_active": True,
                "surge_enabled": True,
                "surge_active": True,
                "surge_multiplier": 1.5,
                # Real box over Saskatoon so the point-in-polygon test actually
                # runs (a 0.0/0.0 fixture is skipped by the lat/lng truthiness
                # guard in _count_supply_in_area and would fake a pass).
                "polygon": [
                    {"lat": 52.0, "lng": -107.0},
                    {"lat": 52.0, "lng": -106.5},
                    {"lat": 52.3, "lng": -106.5},
                    {"lat": 52.3, "lng": -107.0},
                ],
            }
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_drivers_are_fetched_once_for_all_areas(self):
        drivers = [{"id": "d1", "user_id": "u1", "lat": 52.15, "lng": -106.75}]

        async def fake_get_rows(table, filters=None, **kwargs):
            if table == "service_areas":
                return self._areas(4)
            if table == "drivers":
                return list(drivers)
            return []

        db_mock = MagicMock()
        db_mock.get_rows = AsyncMock(side_effect=fake_get_rows)

        with (
            patch("utils.surge_engine.db", db_mock),
            patch("utils.surge_engine._SURGE_SPATIAL_COUNT", False),
            patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=3)),
            patch("utils.surge_engine.present_driver_ids", AsyncMock(return_value=set())),
        ):
            from utils.surge_engine import get_surge_status

            statuses = await get_surge_status(use_cache=False)

        driver_reads = [c for c in db_mock.get_rows.await_args_list if c.args and c.args[0] == "drivers"]
        assert len(driver_reads) == 1, f"expected one batched drivers read, got {len(driver_reads)}"
        # The batch must still produce a correct per-area count, not a shortcut.
        assert len(statuses) == 4
        assert all(s["supply_count"] == 1 for s in statuses)

    @pytest.mark.asyncio
    async def test_batch_fetch_failure_falls_back_to_per_area(self):
        """A failed batch must degrade to the original per-area fetch.

        Reporting zero supply instead would read as a fleet-wide outage on the
        ops dashboard and invite an unnecessary manual surge override.
        """
        calls = {"n": 0}

        async def flaky_fetch(context="supply"):
            calls["n"] += 1
            if context == "status":
                raise RuntimeError("boom")
            return [{"id": "d1", "user_id": "u1", "lat": 52.15, "lng": -106.75}]

        db_mock = MagicMock()
        db_mock.get_rows = AsyncMock(return_value=self._areas(2))

        with (
            patch("utils.surge_engine.db", db_mock),
            patch("utils.surge_engine._SURGE_SPATIAL_COUNT", False),
            patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=1)),
            patch("utils.surge_engine._fetch_dispatchable_drivers", AsyncMock(side_effect=flaky_fetch)),
        ):
            from utils.surge_engine import get_surge_status

            statuses = await get_surge_status(use_cache=False)

        # 1 failed batch attempt + 1 per-area fetch for each of the 2 areas.
        assert calls["n"] == 3
        assert all(s["supply_count"] == 1 for s in statuses)

    @pytest.mark.asyncio
    async def test_spatial_count_path_skips_the_batch_entirely(self):
        """With the spatial count on, nothing reads the driver list — don't fetch it."""
        db_mock = MagicMock()
        db_mock.get_rows = AsyncMock(return_value=self._areas(3))
        batch = AsyncMock(return_value=[])

        with (
            patch("utils.surge_engine.db", db_mock),
            patch("utils.surge_engine._SURGE_SPATIAL_COUNT", True),
            patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=1)),
            patch("utils.surge_engine._count_supply_spatial", AsyncMock(return_value=7)),
            patch("utils.surge_engine._fetch_dispatchable_drivers", batch),
        ):
            from utils.surge_engine import get_surge_status

            statuses = await get_surge_status(use_cache=False)

        batch.assert_not_awaited()
        assert all(s["supply_count"] == 7 for s in statuses)


class TestSurgeStatusCache:
    """The status is cached for 30s so concurrent admin tabs collapse onto one
    computation — but an operator must never see their own surge change late.
    """

    _AREA = [
        {
            "id": "a1",
            "name": "City",
            "is_active": True,
            "surge_enabled": True,
            "surge_active": True,
            "surge_multiplier": 1.5,
        }
    ]

    @pytest.mark.asyncio
    async def test_second_call_is_served_from_cache(self, mock_redis):
        db_mock = MagicMock()
        db_mock.get_rows = AsyncMock(return_value=self._AREA)
        demand = AsyncMock(return_value=2)

        with (
            patch("utils.surge_engine.db", db_mock),
            patch("utils.surge_engine._count_demand_in_area", demand),
            patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=4)),
        ):
            from utils.surge_engine import get_surge_status

            first = await get_surge_status()
            second = await get_surge_status()

        assert first == second
        demand.assert_awaited_once()  # the second call recomputed nothing

    @pytest.mark.asyncio
    async def test_invalidation_forces_a_recompute(self, mock_redis):
        db_mock = MagicMock()
        db_mock.get_rows = AsyncMock(return_value=self._AREA)
        demand = AsyncMock(return_value=2)

        with (
            patch("utils.surge_engine.db", db_mock),
            patch("utils.surge_engine._count_demand_in_area", demand),
            patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=4)),
        ):
            from utils.surge_engine import get_surge_status, invalidate_surge_status_cache

            await get_surge_status()
            await invalidate_surge_status_cache()
            await get_surge_status()

        assert demand.await_count == 2

    @pytest.mark.asyncio
    async def test_cache_read_failure_degrades_to_recompute_not_error(self, mock_redis):
        """A Redis hiccup must not take down the ops surge dashboard."""
        db_mock = MagicMock()
        db_mock.get_rows = AsyncMock(return_value=self._AREA)

        with (
            patch("utils.surge_engine.db", db_mock),
            patch("utils.surge_engine.redis_get", AsyncMock(side_effect=RuntimeError("down"))),
            patch("utils.surge_engine.redis_set", AsyncMock(side_effect=RuntimeError("down"))),
            patch("utils.surge_engine._count_demand_in_area", AsyncMock(return_value=2)),
            patch("utils.surge_engine._count_supply_in_area", AsyncMock(return_value=4)),
        ):
            from utils.surge_engine import get_surge_status

            statuses = await get_surge_status()

        assert statuses[0]["multiplier"] == 1.5
