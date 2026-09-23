"""Unit tests for the two insurance-period close helpers added for the
2026-09-22 insurance-period audit (BLOCKER #1 / HIGH #2).

* ``close_period_after_release`` — the 0-vs-1 close split out of
  ``release_driver_and_close_period`` for callers that issue their own
  ``set_driver_available`` write (completion increments ``total_rides`` in it).
* ``close_period_for_forced_offline`` — re-classifies a driver the system or
  an admin just forced ``is_online=False``. Closes a stale Period 1 to 0, but
  leaves an open Period 2/3 alone: suspending an account does not remove a
  passenger from the car, and the ride-end path closes it later.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.utils import insurance_periods as mod
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    from utils import insurance_periods as mod  # type: ignore

pytestmark = pytest.mark.anyio

DRIVER = "drv-gap-1"
RIDE = "ride-gap-1"


def _get_rows_returning(rides=None, offers=None, raises=None):
    async def _get_rows(table, filters=None, **kw):
        if raises is not None:
            raise raises
        if table == "rides":
            return rides or []
        if table == "ride_offers":
            return offers or []
        return []

    return AsyncMock(side_effect=_get_rows)


class TestClosePeriodAfterRelease:
    async def test_offline_row_closes_to_period_0(self):
        record = AsyncMock()
        with patch.object(mod, "record_period_transition", record):
            result = await mod.close_period_after_release(
                DRIVER, {"id": DRIVER, "is_online": False, "is_available": False}, reason="ride_completed"
            )
        assert result == 0
        record.assert_awaited_once_with(DRIVER, 0)

    async def test_online_row_closes_to_period_1(self):
        record = AsyncMock()
        with patch.object(mod, "record_period_transition", record):
            result = await mod.close_period_after_release(
                DRIVER, {"id": DRIVER, "is_online": True, "is_available": True}, reason="driver_cancelled"
            )
        assert result == 1
        record.assert_awaited_once_with(DRIVER, 1)

    async def test_row_without_is_online_falls_back_to_clamped_is_available(self):
        record = AsyncMock()
        with patch.object(mod, "record_period_transition", record):
            result = await mod.close_period_after_release(
                DRIVER, {"id": DRIVER, "is_available": False}, reason="rider_noshow"
            )
        assert result == 0
        record.assert_awaited_once_with(DRIVER, 0)

    async def test_unreadable_row_writes_nothing(self):
        record = AsyncMock()
        with patch.object(mod, "record_period_transition", record):
            result = await mod.close_period_after_release(DRIVER, None, reason="ride_completed")
        assert result is None
        record.assert_not_awaited()

    @pytest.mark.parametrize(
        "reason",
        ["driver_cancelled", "rider_noshow", "ride_completed", "rider_completed", "admin_cancelled", "admin_completed"],
    )
    async def test_every_new_call_site_label_is_accepted(self, reason):
        record = AsyncMock()
        with patch.object(mod, "record_period_transition", record):
            assert await mod.close_period_after_release(DRIVER, {"is_online": True}, reason=reason) == 1

    async def test_unknown_reason_is_programmer_error(self):
        with pytest.raises(ValueError):
            await mod.close_period_after_release(DRIVER, {"is_online": True}, reason="bogus")


class TestClosePeriodForForcedOffline:
    async def test_no_ride_no_offer_closes_to_period_0(self):
        """The core BLOCKER #1 case: an idle online driver (open Period 1) is
        suspended. Before the fix nothing was written and the Period 1 row
        stayed open forever."""
        record = AsyncMock()
        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows_returning()),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.close_period_for_forced_offline(DRIVER, reason="admin_suspend")
        assert result == 0
        record.assert_awaited_once_with(DRIVER, 0)

    async def test_passenger_aboard_leaves_period_3_open(self):
        """Suspension does not remove a passenger from the car; closing the
        Period 3 to 0 would assert personal-auto-only cover mid-trip. No write
        at all — re-asserting 3 could race a concurrent completion."""
        record = AsyncMock()
        rides = [{"id": RIDE, "status": "in_progress"}]
        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows_returning(rides=rides)),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.close_period_for_forced_offline(DRIVER, reason="document_expired")
        assert result == 3
        record.assert_not_awaited()

    @pytest.mark.parametrize("status", ["driver_assigned", "driver_accepted", "driver_arrived"])
    async def test_en_route_leaves_period_2_open(self, status):
        record = AsyncMock()
        rides = [{"id": RIDE, "status": status}]
        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows_returning(rides=rides)),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.close_period_for_forced_offline(DRIVER, reason="admin_ban")
        assert result == 2
        record.assert_not_awaited()

    async def test_pending_batch_offer_leaves_period_2_open(self):
        """Batch dispatch holds the claim in ride_offers with no rides.driver_id
        link — same rule as the reconciler's _pending_offer_candidates."""
        record = AsyncMock()
        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows_returning(offers=[{"ride_id": RIDE}])),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.close_period_for_forced_offline(DRIVER, reason="admin_reject")
        assert result == 2
        record.assert_not_awaited()

    async def test_ride_query_is_scoped_to_driver_and_obligated_statuses(self):
        get_rows = _get_rows_returning()
        with (
            patch.object(mod.db_supabase, "get_rows", get_rows),
            patch.object(mod, "record_period_transition", AsyncMock()),
        ):
            await mod.close_period_for_forced_offline(DRIVER, reason="admin_suspend")
        table, filters = get_rows.await_args_list[0].args[:2]
        assert table == "rides"
        assert filters["driver_id"] == DRIVER
        assert set(filters["status"]["$in"]) == {"driver_assigned", "driver_accepted", "driver_arrived", "in_progress"}

    async def test_lookup_failure_writes_nothing_and_does_not_raise(self):
        """A guessed Period 0 could close a real Period 3 — write nothing."""
        record = AsyncMock()
        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows_returning(raises=RuntimeError("db down"))),
            patch.object(mod, "record_period_transition", record),
        ):
            result = await mod.close_period_for_forced_offline(DRIVER, reason="admin_status_override")
        assert result is None
        record.assert_not_awaited()

    async def test_unknown_reason_is_programmer_error(self):
        with pytest.raises(ValueError):
            await mod.close_period_for_forced_offline(DRIVER, reason="bogus")
