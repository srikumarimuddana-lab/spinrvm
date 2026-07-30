"""Driver account deletion must take the driver row out of service.

Setting `drivers.deleted_at` alone was not enough. There is no 'deleted'
value in the driver status set, so a tombstoned driver kept
`status='active'`, `is_online=true`, `is_available=true` and stayed
indistinguishable from a working driver on every bulk `get_rows` read —
including the dispatch candidate query, which reads `drivers` directly and
never sees the users row's `status='pending_deletion'`.

Two guarantees are pinned here:
  1. deletion clears the intent flags dispatch reads AND closes the open
     `driver_insurance_periods` row (the append-only 7-year SGI/insurance
     audit trail), rather than waiting up to `stale_intent_offline_hours`
     (default 4h) for the reconciler loop to notice.
  2. the dispatch candidate query filters `deleted_at IS NULL` regardless,
     so the guard survives a row whose flags were never written (admin edit,
     bulk import, a partially-applied deletion).
"""

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.routes import users as users_route
    from backend.services.dispatch_service import DispatchService
except ImportError:  # pragma: no cover - bare-path test runs
    from routes import users as users_route  # type: ignore
    from services.dispatch_service import DispatchService  # type: ignore

_USERS_MOD = users_route.__name__

DRIVER = {"id": "drv-1", "user_id": "usr-1", "status": "active", "is_online": True}


class TestTombstoneDriverRow:
    async def _run(self, driver):
        with (
            patch.object(
                users_route.db_supabase, "get_rows", AsyncMock(return_value=([driver] if driver else []))
            ) as rows,
            patch.object(users_route.db_supabase, "update_one", AsyncMock()) as upd,
            patch.object(users_route.db_supabase, "invalidate_driver_cache", AsyncMock()) as inval,
            patch(f"{_USERS_MOD}.record_period_transition", AsyncMock()) as period,
        ):
            result = await users_route._tombstone_driver_row("usr-1", "2026-07-30T00:00:00Z")
        return result, upd, period, rows, inval

    @pytest.mark.anyio
    async def test_clears_intent_flags_dispatch_reads(self):
        _, upd, _, _, _ = await self._run(DRIVER)
        upd.assert_awaited_once()
        table, where, payload = upd.await_args.args
        assert table == "drivers"
        # Keyed by drivers.id, not user_id — one row, unambiguously.
        assert where == {"id": "drv-1"}
        assert payload["deleted_at"] == "2026-07-30T00:00:00Z"
        # These four are what dispatch_service.find_candidate_drivers filters on.
        assert payload["is_online"] is False
        assert payload["is_available"] is False
        assert payload["went_offline_at"] == "2026-07-30T00:00:00Z"

    @pytest.mark.anyio
    async def test_closes_open_insurance_period(self):
        _, _, period, _, _ = await self._run(DRIVER)
        # Period 0 = out of the app entirely, personal auto insurance only.
        period.assert_awaited_once_with("drv-1", 0)

    @pytest.mark.anyio
    async def test_evicts_both_driver_cache_keys(self):
        """The write is keyed by `id`, so `_pre_invalidate_for_table` only evicts
        the by-driver_id key. The by-user_id entry (30s TTL, read by
        get_current_user on every request) would otherwise keep serving the
        pre-deletion row."""
        _, _, _, _, inval = await self._run(DRIVER)
        inval.assert_awaited_once_with(driver_id="drv-1", user_id="usr-1")

    @pytest.mark.anyio
    async def test_lookup_excludes_already_deleted_rows(self):
        _, _, _, rows, _ = await self._run(DRIVER)
        assert rows.await_args.args[1] == {"user_id": "usr-1", "deleted_at": None}

    @pytest.mark.anyio
    async def test_non_driver_account_is_a_noop(self):
        """A rider has no drivers row — deletion must not write one or record a
        period transition for an id that does not exist. Same path a repeat
        deletion takes, since the lookup filters `deleted_at IS NULL`: no
        re-stamped tombstone and no extra row appended to the insurance-period
        audit table."""
        result, upd, period, _, inval = await self._run(None)
        assert result is None
        upd.assert_not_awaited()
        period.assert_not_awaited()
        inval.assert_not_awaited()


class TestDeletionGuards:
    """The driver app already told drivers deletion was rejected on an active
    ride / unsettled balance / pending payout. Nothing on the backend checked."""

    async def _assert(self, *, rides_by_filter=None, payouts=None, balance="0.00", driver=DRIVER):
        rides_by_filter = rides_by_filter or {}

        async def _get_rows(table, filters=None, limit=None, **kw):
            if table == "drivers":
                return [driver] if driver else []
            if table == "payouts":
                return payouts or []
            if table == "rides":
                key = "driver_id" if "driver_id" in (filters or {}) else "rider_id"
                return rides_by_filter.get(key) or []
            return []

        with (
            patch.object(users_route.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch(
                f"{_USERS_MOD}.db_supabase.get_rows",
                AsyncMock(side_effect=_get_rows),
            ),
            patch("routes.drivers.earnings.get_driver_balance", AsyncMock(return_value={"payable_balance": balance})),
        ):
            await users_route._assert_deletable("usr-1")

    @pytest.mark.anyio
    async def test_clean_account_passes(self):
        await self._assert()

    @pytest.mark.anyio
    async def test_rider_with_active_ride_is_blocked(self):
        with pytest.raises(Exception) as ei:
            await self._assert(rides_by_filter={"rider_id": [{"id": "ride-1", "status": "in_progress"}]})
        assert ei.value.status_code == 409
        assert "ride in progress" in ei.value.detail

    @pytest.mark.anyio
    async def test_driver_with_active_ride_is_blocked(self):
        with pytest.raises(Exception) as ei:
            await self._assert(rides_by_filter={"driver_id": [{"id": "ride-1", "status": "driver_arrived"}]})
        assert ei.value.status_code == 409

    @pytest.mark.anyio
    async def test_pending_payout_is_blocked(self):
        with pytest.raises(Exception) as ei:
            await self._assert(payouts=[{"id": "p1", "status": "pending"}])
        assert ei.value.status_code == 409
        assert "payout" in ei.value.detail

    @pytest.mark.anyio
    async def test_positive_balance_is_blocked(self):
        """Deletion revokes every token, so a driver who deletes holding a
        balance can no longer reach the payout screen to claim it."""
        with pytest.raises(Exception) as ei:
            await self._assert(balance="42.50")
        assert ei.value.status_code == 409
        assert "42.50" in ei.value.detail

    @pytest.mark.anyio
    async def test_negative_balance_does_not_block(self):
        """An over-paid driver owes Spinr, not the other way round — that is a
        recovery problem, not a reason to trap the account."""
        await self._assert(balance="-5.00")

    @pytest.mark.anyio
    async def test_rider_without_driver_row_skips_driver_checks(self):
        """A rider has no drivers row; the payout/balance lookups must not run
        (and must not 404 inside get_driver_balance)."""
        await self._assert(driver=None, payouts=[{"id": "p1", "status": "pending"}], balance="99.00")


class TestDispatchExcludesDeletedDrivers:
    @pytest.mark.anyio
    async def test_candidate_query_filters_deleted_at_is_null(self):
        captured = {}

        async def _get_rows(table, filters=None, **kw):
            captured["table"] = table
            captured["filters"] = filters
            return []

        db = type("_DB", (), {"get_rows": staticmethod(_get_rows)})()
        svc = DispatchService(db)
        await svc.find_candidate_drivers({"id": "ride-1", "vehicle_type_id": "vt-1"})

        assert captured["table"] == "drivers"
        # None compiles to PostgREST `is.null` in repositories/_base.py's
        # _apply_filters — NOT `= NULL`, which would match zero rows and take
        # every driver out of dispatch.
        assert captured["filters"]["deleted_at"] is None
        # The pre-existing gates must still be present — this filter is an
        # addition, not a replacement.
        assert captured["filters"]["is_online"] is True
        assert captured["filters"]["is_available"] is True
        assert captured["filters"]["is_verified"] is True
        assert captured["filters"]["status"] == "active"

    @pytest.mark.anyio
    async def test_wav_request_keeps_the_deleted_filter(self):
        captured = {}

        async def _get_rows(table, filters=None, **kw):
            captured["filters"] = filters
            return []

        db = type("_DB", (), {"get_rows": staticmethod(_get_rows)})()
        svc = DispatchService(db)
        await svc.find_candidate_drivers({"id": "ride-1", "vehicle_type_id": "vt-1", "requires_wav": True})
        assert captured["filters"]["is_wav"] is True
        assert captured["filters"]["deleted_at"] is None
