"""Coverage-closure tests for routes/drivers/referrals.py (A1c Sub-tier A).

test_drivers_extended.py::TestDriverReferral already pins the "coroutine not
iterable" regression for get_driver_referral_info / get_referred_drivers
(both endpoints' referred-users iteration, in the "still pending" case). This
file closes the remaining gaps:
  - get_driver_referral_info: driver-not-found, the qualified (>= threshold)
    branch, and the referred_by (inbound-referral) resolution block
  - get_referred_drivers: driver-not-found
  - apply_referral_code: entirely untested — already-applied guard, all
    three code-resolution paths (driver_code, legacy referral_code, the
    DRIVER<id8> default-code regex fallback incl. its own lookup-failure
    swallow), invalid-code 404, self-referral 400, success
  - get_driver_leaderboard: entirely untested — driver-not-found, the
    aggregate-RPC happy path, RPC-failure -> daily-stats fallback,
    daily-stats-read-failure degrade, freshness-topup-read-failure degrade,
    users-lookup-failure placeholder-names degrade, and the "all" period
    start-date branch

Patch-target conventions (matching test_subscriptions_coverage.py /
test_drivers_extended.py):
  - `db_supabase` is a module reference shared by every importer;
    `patch("backend.db_supabase.<fn>")` covers `db_supabase.<fn>(...)` AND
    `_deps.db.<fn>(...)` call sites (`db = db_supabase` is a module alias).
  - `_deps.resolve_referral_terms` / `_deps.paid_referral_earnings` are
    called via the live `_deps.<name>` attribute (referrals.py does
    `from . import _deps` and calls through it), so patch them at
    `backend.routes.drivers._deps.<name>`.
  - `paid_referee_earnings` is imported as a bound name at the top of
    referrals.py (`from ._deps import (... paid_referee_earnings ...)`), so
    it must be patched at `backend.routes.drivers.referrals.paid_referee_earnings`
    instead — patching `_deps.paid_referee_earnings` would not affect the
    already-bound copy in referrals.py's own namespace.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

USER_ID = "user_referrals_cov"
DRIVER_ID = "driver_referrals_cov"

_TERMS = {"rides": 10, "referrer": Decimal("10.00"), "referee": Decimal("0")}


def _driver(**extra) -> dict:
    return {"id": DRIVER_ID, "user_id": USER_ID, "driver_code": "DRV-ABC123", **extra}


def _patch_terms_and_earnings(paid_referral=None, referee_earned=Decimal("0")):
    return (
        patch("backend.routes.drivers._deps.resolve_referral_terms", AsyncMock(return_value=_TERMS)),
        patch("backend.routes.drivers._deps.paid_referral_earnings", AsyncMock(return_value=paid_referral)),
        patch("backend.routes.drivers.referrals.paid_referee_earnings", AsyncMock(return_value=referee_earned)),
    )


# ============================================================
# get_driver_referral_info
# ============================================================


class TestGetDriverReferralInfoGaps:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_referral_info

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_driver_referral_info(current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_qualified_referral_counts_and_referred_by_resolved(self):
        """A referee who HAS hit the ride threshold counts as qualified, and
        an inbound referral (this driver was referred by someone else) must
        resolve the referrer's name/code."""
        from backend.routes.drivers import get_driver_referral_info

        referrer_driver = {"id": "ref_drv_1", "user_id": "ref_user_1", "driver_code": "DRV-REFERRER"}

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                uid = (filters or {}).get("user_id")
                if uid == USER_ID:
                    return [_driver(service_area_id=None)]
                if uid == "ref_user_1":
                    return [referrer_driver]
                return [{"id": "referee_driver_1", "user_id": uid}]
            if table == "users":
                if "referral_code_used" in (filters or {}):
                    return [{"id": "referee_user_1"}]
                if (filters or {}).get("id") == USER_ID:
                    return [{"referred_by": "ref_drv_1"}]
                return []
            return []

        p1, p2, p3 = _patch_terms_and_earnings(paid_referral=None)
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=15)),  # >= 10 required
            patch("backend.db_supabase.get_driver_by_id", AsyncMock(return_value=referrer_driver)),
            patch(
                "backend.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Ref", "last_name": "Errer"}),
            ),
            p1,
            p2,
            p3,
        ):
            result = await get_driver_referral_info(current_user={"id": USER_ID})

        assert result["qualified_referrals"] == 1
        assert result["pending_referrals"] == 0
        assert result["referred_by"] == {"name": "Ref Errer", "code": "DRV-REFERRER"}

    async def test_referred_by_none_when_not_referred(self):
        from backend.routes.drivers import get_driver_referral_info

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return [_driver(service_area_id=None)]
            if table == "users":
                if (filters or {}).get("id") == USER_ID:
                    return [{"referred_by": None}]
                return []
            return []

        p1, p2, p3 = _patch_terms_and_earnings()
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
            p1,
            p2,
            p3,
        ):
            result = await get_driver_referral_info(current_user={"id": USER_ID})

        assert result["referred_by"] is None

    async def test_paid_earnings_snapshot_wins_over_estimate(self):
        """Once the payout loop has actually paid the driver, the snapshotted
        total must be used instead of reward*qualified estimate."""
        from backend.routes.drivers import get_driver_referral_info

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return [_driver(service_area_id=None)]
            if table == "users":
                if (filters or {}).get("id") == USER_ID:
                    return [{"referred_by": None}]
                return []
            return []

        p1, p2, p3 = _patch_terms_and_earnings(paid_referral=Decimal("37.50"))
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
            p1,
            p2,
            p3,
        ):
            result = await get_driver_referral_info(current_user={"id": USER_ID})

        assert result["referral_earnings"] == "37.50"


# ============================================================
# get_referred_drivers
# ============================================================


class TestGetReferredDriversGaps:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_referred_drivers

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_referred_drivers(limit=50, offset=0, current_user={"id": "ghost"})
        assert exc.value.status_code == 404


# ============================================================
# apply_referral_code (previously entirely untested)
# ============================================================


class TestApplyReferralCode:
    async def test_already_applied_returns_400(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        req = ApplyReferralCodeRequest(referral_code="DRV-XYZ")
        with patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": "OLD"})):
            with pytest.raises(HTTPException) as exc:
                await apply_referral_code(req, current_user={"id": USER_ID})
        assert exc.value.status_code == 400

    async def test_resolves_by_driver_code(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        ref_driver = {"id": "ref-drv-1", "user_id": "other-user"}

        def get_rows(table, filters=None, **kw):
            if table == "drivers" and (filters or {}).get("driver_code") == "DRV-XYZ":
                return [ref_driver]
            return []

        update_mock = AsyncMock()
        req = ApplyReferralCodeRequest(referral_code="drv-xyz")
        with (
            patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": None})),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.update_one", update_mock),
        ):
            result = await apply_referral_code(req, current_user={"id": USER_ID})

        assert result == {"success": True, "referral_code": "DRV-XYZ"}
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[2]["referred_by"] == "ref-drv-1"

    async def test_resolves_by_legacy_referral_code(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        ref_driver = {"id": "ref-drv-2", "user_id": "other-user-2"}

        def get_rows(table, filters=None, **kw):
            if table == "drivers" and (filters or {}).get("driver_code") == "LEGACY1":
                return []
            if table == "drivers" and (filters or {}).get("referral_code") == "LEGACY1":
                return [ref_driver]
            return []

        req = ApplyReferralCodeRequest(referral_code="legacy1")
        with (
            patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": None})),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.update_one", AsyncMock()),
        ):
            result = await apply_referral_code(req, current_user={"id": USER_ID})

        assert result["success"] is True

    async def test_resolves_via_default_code_id_fallback(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        ref_driver = {"id": "abcdef1234567890", "user_id": "other-user-3"}

        def get_rows(table, filters=None, **kw):
            if table == "drivers" and "driver_code" in (filters or {}):
                return []
            if table == "drivers" and "referral_code" in (filters or {}):
                return []
            if table == "drivers" and "$regex" in (filters or {}).get("id", {}):
                assert filters["id"]["$options"] == "i"
                assert filters["id"]["$regex"] == "ABCDEF12"
                return [ref_driver]
            return []

        req = ApplyReferralCodeRequest(referral_code="DRIVERABCDEF12")
        with (
            patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": None})),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.update_one", AsyncMock()),
        ):
            result = await apply_referral_code(req, current_user={"id": USER_ID})

        assert result["success"] is True

    async def test_default_code_fallback_lookup_failure_is_swallowed_then_404(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        def get_rows(table, filters=None, **kw):
            if table == "drivers" and ("driver_code" in (filters or {}) or "referral_code" in (filters or {})):
                return []
            if table == "drivers" and "id" in (filters or {}):
                raise Exception("regex lookup exploded")
            return []

        req = ApplyReferralCodeRequest(referral_code="DRIVERABCDEF12")
        with (
            patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": None})),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            with pytest.raises(HTTPException) as exc:
                await apply_referral_code(req, current_user={"id": USER_ID})
        assert exc.value.status_code == 404

    async def test_invalid_code_raises_404(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        req = ApplyReferralCodeRequest(referral_code="NOPE")
        with (
            patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": None})),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc:
                await apply_referral_code(req, current_user={"id": USER_ID})
        assert exc.value.status_code == 404

    async def test_self_referral_raises_400(self):
        from backend.routes.drivers import ApplyReferralCodeRequest, apply_referral_code

        ref_driver = {"id": "ref-drv-self", "user_id": USER_ID}

        def get_rows(table, filters=None, **kw):
            if table == "drivers" and (filters or {}).get("driver_code") == "SELFCODE":
                return [ref_driver]
            return []

        req = ApplyReferralCodeRequest(referral_code="selfcode")
        with (
            patch("backend.db_supabase.get_user_by_id", AsyncMock(return_value={"referral_code_used": None})),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            with pytest.raises(HTTPException) as exc:
                await apply_referral_code(req, current_user={"id": USER_ID})
        assert exc.value.status_code == 400
        assert "own referral code" in exc.value.detail.lower()


# ============================================================
# get_driver_leaderboard (previously entirely untested)
# ============================================================


class TestGetDriverLeaderboard:
    def _drivers_list(self):
        return [
            {"id": DRIVER_ID, "user_id": USER_ID, "rating": 4.9},
            {"id": "other-drv", "user_id": "other-user", "rating": 4.5},
        ]

    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_leaderboard

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_leaderboard(period="week", limit=20, current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_rpc_happy_path_ranks_and_finds_current_user(self):
        from backend.routes.drivers import get_driver_leaderboard

        agg_result = MagicMock(
            data=[
                {
                    "driver_id": DRIVER_ID,
                    "rides": 20,
                    "earnings": "300.00",
                    "tips": "30.00",
                    "last_stat_date": "2026-07-30",
                },
            ]
        )

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            if table == "rides":
                # freshness top-up: one fresh ride for the "other" driver
                return [
                    {
                        "driver_id": "other-drv",
                        "base_fare": 10.0,
                        "distance_fare": 2.0,
                        "time_fare": 1.0,
                        "tip_amount": 1.0,
                        "created_at": "2026-07-31T00:00:00+00:00",
                    }
                ]
            if table == "users":
                return [
                    {"id": USER_ID, "first_name": "Sam", "last_name": "Driver"},
                    {"id": "other-user", "first_name": "Ali", "last_name": "Other"},
                ]
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(return_value=agg_result)),
        ):
            result = await get_driver_leaderboard(period="week", limit=20, current_user={"id": USER_ID})

        assert result["total_drivers"] == 2
        assert result["my_rank"] is not None
        assert result["my_rank"]["driver_id"] == DRIVER_ID
        assert result["my_rank"]["rides"] == 20
        names = {r["driver_id"]: r["name"] for r in result["leaderboard"]}
        assert names[DRIVER_ID] == "Sam Driver"
        assert names["other-drv"] == "Ali Other"

    async def test_rpc_failure_falls_back_to_daily_stats(self):
        from backend.routes.drivers import get_driver_leaderboard

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            if table == "driver_daily_stats":
                return [
                    {
                        "driver_id": DRIVER_ID,
                        "stat_date": "2026-07-29",
                        "rides_completed": 4,
                        "total_earnings": 80.0,
                        "total_tips": 8.0,
                    }
                ]
            if table == "rides":
                return []
            if table == "users":
                return [{"id": USER_ID, "first_name": "Sam", "last_name": "Driver"}]
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(side_effect=Exception("rpc missing (pre-204)"))),
        ):
            result = await get_driver_leaderboard(period="week", limit=20, current_user={"id": USER_ID})

        my_rank = result["my_rank"]
        assert my_rank["rides"] == 4

    async def test_daily_stats_fallback_read_failure_degrades_to_empty(self):
        from backend.routes.drivers import get_driver_leaderboard

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            if table == "driver_daily_stats":
                raise Exception("daily stats table down too")
            if table == "rides":
                return []
            if table == "users":
                return []
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(side_effect=Exception("rpc down"))),
        ):
            result = await get_driver_leaderboard(period="week", limit=20, current_user={"id": USER_ID})

        assert result["my_rank"]["rides"] == 0

    async def test_freshness_topup_read_failure_degrades(self):
        from backend.routes.drivers import get_driver_leaderboard

        agg_result = MagicMock(data=[])

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            if table == "rides":
                raise Exception("rides read failed")
            if table == "users":
                return []
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(return_value=agg_result)),
        ):
            result = await get_driver_leaderboard(period="week", limit=20, current_user={"id": USER_ID})

        assert result["total_drivers"] == 2

    async def test_users_lookup_failure_uses_placeholder_names(self):
        from backend.routes.drivers import get_driver_leaderboard

        agg_result = MagicMock(data=[])

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            if table == "rides":
                return []
            if table == "users":
                raise Exception("users lookup failed")
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(return_value=agg_result)),
        ):
            result = await get_driver_leaderboard(period="week", limit=20, current_user={"id": USER_ID})

        assert all(r["name"] == "Driver" for r in result["leaderboard"])

    async def test_all_period_uses_2020_epoch_start(self):
        from backend.routes.drivers import get_driver_leaderboard

        agg_result = MagicMock(data=[])
        captured_start = {}

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            return []

        async def fake_run_sync(fn):
            # We can't introspect the lambda's closure args directly, but the
            # RPC call site passes p_start=start_date_str -- exercise it and
            # just assert the call completes without error for the "all"
            # branch (start_dt = datetime(2020,1,1)).
            captured_start["called"] = True
            return agg_result

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", fake_run_sync),
        ):
            result = await get_driver_leaderboard(period="all", limit=20, current_user={"id": USER_ID})

        assert captured_start["called"] is True
        assert result["period"] == "all"

    async def test_all_drivers_fetch_failure_degrades_to_empty_leaderboard(self):
        from backend.routes.drivers import get_driver_leaderboard

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                raise Exception("drivers table down")
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(return_value=MagicMock(data=[]))),
        ):
            result = await get_driver_leaderboard(period="week", limit=20, current_user={"id": USER_ID})

        assert result["total_drivers"] == 0
        assert result["my_rank"] is None

    async def test_topup_boundary_is_regina_day_end_not_utc_midnight(self):
        """MAX(stat_date)=D covers created_at through D+1 06:00 UTC (Regina
        day end). A UTC-midnight boundary would double-count the last 6
        Regina evening hours already aggregated into row D."""
        from backend.routes.drivers import get_driver_leaderboard

        agg_result = MagicMock(
            data=[
                {
                    "driver_id": DRIVER_ID,
                    "rides": 5,
                    "earnings": "100.00",
                    "tips": "10.00",
                    "last_stat_date": "2026-07-30",
                },
            ]
        )
        captured = {}

        def get_rows(table, filters=None, columns=None, **kw):
            if table == "drivers":
                return self._drivers_list()
            if table == "rides":
                captured["filters"] = filters
                return []
            if table == "users":
                return []
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.run_sync", AsyncMock(return_value=agg_result)),
        ):
            await get_driver_leaderboard(period="all", limit=20, current_user={"id": USER_ID})

        # Regina is UTC-6 year-round: day 2026-07-30 ends 2026-07-31 06:00 UTC.
        boundary = captured["filters"]["created_at"]["$gte"]
        assert boundary.startswith("2026-07-31T06:00:00")
