"""Coverage-closure tests for routes/drivers/ride_flow.py, ride_cancel.py, and
ride_reads.py (A1c Sub-tier A — the rest of the unevenly-covered
routes/drivers/ package).

Complements the existing tests that already exercise these three files'
happy paths and a handful of guard branches:
- test_drivers_extended.py: TestArriveAtPickup, TestStartRide, TestCancelRide,
  TestDeclineRide (partial), TestRateRider, TestGetActiveRide (partial),
  TestGetRideHistory (partial)
- test_ride_accept_flow.py: accept_ride's idempotent-replay / double-accept /
  searching-claim-filter branches
- test_subscription_enforcement.py: accept_ride's basic subscription-required
  gate (no-sub-402 / active-sub-passes / not-required-passes)
- test_c2_driver_cancel_atomic.py: driver cancel_ride / mark_rider_noshow
  atomic-claim-lost (409) + IDOR guards
- test_active_ride_rider_pii.py: get_active_ride's rider-field allowlist
- test_rides.py::test_full_ride_lifecycle: accept -> arrive -> verify-otp ->
  complete happy path (no service_area_id, so the subscription guard is
  skipped there)

This file fills in the branches those don't reach: accept_ride's
parent-area-inheritance / expired-sub / plan-scope-mismatch / DB-error-503
subscription sub-branches, the batch-dispatch offer-cleanup winner/loser
paths, the ride_metrics pickup-leg write, decline_ride's early-resolution
re-dispatch branches and its own 404/409/403 guards, verify_pickup_otp
(previously untested as a standalone unit), arrive_at_pickup's geofence
rejection, start_ride's production-blocked 410, mark_rider_noshow's full
success path (fee calc, wallet debit, driver payout — previously only the
409-claim-lost branch had coverage), and get_active_ride's batch-offer
fallback / incentives / quest-hint / service-area-polygon enrichment plus
get_ride_history's incentive-claims / earnings-snapshot branches.

Patch-target conventions (see routes/drivers/_deps.py + CLAUDE.md, and the
docstring at the top of test_subscriptions_coverage.py for the fuller
writeup):
- `db_supabase` is a *module reference* shared by every importer, so
  `patch("backend.routes.drivers._deps.db_supabase.<fn>")` affects both
  `db_supabase.<fn>(...)` and `_deps.db.<fn>(...)` call sites in these files.
- `_deps.manager`, `_deps.record_period_transition`, `_deps.send_push_notification`,
  `_deps.reset_miss_streak`, `_deps.cancel_authorization`, `_deps.spawn` are
  *bound names* copied into the _deps namespace at import time, so they must
  be patched at `backend.routes.drivers._deps.<name>`.
- `update_acceptance_rate` / `match_driver_to_ride` / cancellation-service
  helpers are imported *inside* the function bodies on every call (dual-import
  pattern), so patching the *source* module is what's needed.

Test-only change — no application code modified.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_DRIVER_ID = "drv-cov-1"
_USER_ID = "user-cov-1"
_RIDE_ID = "ride-cov-1"
_RIDER_ID = "rider-cov-1"


class _Patches:
    """Enter a list of patch() context managers together, exposing their mock
    objects by index. Needed because `with (*tuple_of_ctx_managers, patch(...)
    as x):` is invalid Python syntax (a starred expression cannot appear
    alongside an `as`-bound item in a parenthesized `with`) -- this sidesteps
    that by using a single ExitStack-backed context manager instead of
    unpacking a helper's returned tuple of patches directly into `with (...)`.
    """

    def __init__(self, *ctx_managers):
        self._ctx_managers = ctx_managers
        self._stack = None

    def __enter__(self):
        self._stack = ExitStack()
        return [self._stack.enter_context(cm) for cm in self._ctx_managers]

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _driver(**kw):
    # is_online=True by default: this fixture represents the normal,
    # legitimately-online driver on every accept/decline/arrive path in this
    # file. Tests exercising the offline-rejection guard (accept_ride) pass
    # is_online=False explicitly — see TestAcceptRideGuards.
    base = {
        "id": _DRIVER_ID,
        "user_id": _USER_ID,
        "lat": 52.1,
        "lng": -106.6,
        "status": "active",
        "is_online": True,
    }
    base.update(kw)
    return base


def _ride(status="driver_assigned", **kw):
    base = {
        "id": _RIDE_ID,
        "status": status,
        "driver_id": _DRIVER_ID,
        "rider_id": _RIDER_ID,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
        "vehicle_type_id": "vt-1",
    }
    base.update(kw)
    return base


def _spawn_close(coro):
    """spawn() replacement that just closes the coroutine (no leaked coro warning)."""
    coro.close()


# ============================================================
# accept_ride
# ============================================================


class TestAcceptRideGuards:
    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404
        assert "Driver not found" in exc.value.detail

    async def test_403_suspended_driver(self):
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import AccountDisabledException

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(status="suspended")]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        ):
            with pytest.raises(AccountDisabledException):
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

    async def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_rejects_accept_from_an_offline_driver(self):
        """2026-08-18 fleet audit ranked blocker #4: a driver who went
        offline (is_online=False) after being claimed for an offer must not
        be able to accept it — a stale queued push-notification tap or a
        plain retry must not strand the rider with a driver who never
        shows up."""
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import DriverOfflineException

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(is_online=False)]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        ):
            with pytest.raises(DriverOfflineException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.details == {"driver_id": _DRIVER_ID}

    async def test_offline_check_runs_before_the_ride_lookup(self):
        """The offline check uses only the already-fetched driver row, so it
        must reject even when the ride itself doesn't exist yet (no wasted
        work, and no accidental information leak about ride existence to an
        offline driver)."""
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import DriverOfflineException

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(is_online=False)]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(DriverOfflineException):
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

    async def test_403_cannot_accept_own_ride(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(rider_id=_USER_ID)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 403

    async def test_idempotent_replay_when_already_accepted_by_this_driver(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="driver_accepted", driver_id=_DRIVER_ID)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True, "already_accepted": True}


class TestAcceptRideSubscriptionGuardBranches:
    """The child-area-inherits-from-parent, expired-sub, and plan-scope
    (service_areas / vehicle_types allowlist) sub-branches of the
    subscription-required gate. test_subscription_enforcement.py already
    covers the top-level required/not-required/active-sub-passes cases."""

    def _patches(self, ride, get_rows_map, find_one_map, *, quota_ok=True):
        async def fake_get_rows(table, filters=None, **kw):
            return get_rows_map.get(table, [])

        async def fake_find_one(table, filters=None, **kw):
            key = (table, filters.get("id")) if filters else (table, None)
            return find_one_map.get(key)

        quota_mock = AsyncMock() if quota_ok else AsyncMock(side_effect=RuntimeError("quota db down"))
        return (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(side_effect=fake_find_one)),
            patch("backend.utils.spinr_pass.assert_quota_available", quota_mock),
        )

    async def test_child_area_inherits_subscription_required_from_parent_blocks_without_sub(self):
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import SpinrException

        ride = _ride(status="searching", driver_id=None, service_area_id="area-child")
        child_area = {"id": "area-child", "subscription_required": False, "parent_service_area_id": "area-parent"}
        parent_area = {"id": "area-parent", "subscription_required": True}
        patches = self._patches(
            ride,
            {"drivers": [_driver()], "driver_subscriptions": []},
            {("service_areas", "area-child"): child_area, ("service_areas", "area-parent"): parent_area},
        )
        with _Patches(*patches):
            with pytest.raises(SpinrException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 402

    async def test_expired_active_sub_row_is_marked_expired_and_still_blocks(self):
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import SpinrException

        ride = _ride(status="searching", driver_id=None, service_area_id="area-1")
        area = {"id": "area-1", "subscription_required": True}
        expired_sub = {
            "id": "sub-1",
            "plan_id": "plan-1",
            "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        }
        patches = self._patches(
            ride,
            {"drivers": [_driver()], "driver_subscriptions": [expired_sub]},
            {("service_areas", "area-1"): area},
        )
        with _Patches(
            *patches,
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": "sub-1"})),
        ) as mocks:
            upd = mocks[-1]
            with pytest.raises(SpinrException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 402
        upd.assert_awaited_once()
        assert upd.await_args.args[0] == "driver_subscriptions"
        assert upd.await_args.args[2] == {"status": "expired"}

    async def test_plan_service_area_mismatch_with_no_parent_coverage_blocks(self):
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import SpinrException

        ride = _ride(status="searching", driver_id=None, service_area_id="area-1")
        area = {"id": "area-1", "subscription_required": True}
        active_sub = {"id": "sub-1", "plan_id": "plan-1"}
        plan = {"id": "plan-1", "service_areas": ["area-other"], "vehicle_types": None}
        patches = self._patches(
            ride,
            {"drivers": [_driver()], "driver_subscriptions": [active_sub]},
            {("service_areas", "area-1"): area, ("subscription_plans", "plan-1"): plan},
        )
        with _Patches(*patches):
            with pytest.raises(SpinrException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 402

    async def test_plan_service_area_mismatch_but_covers_parent_area_passes(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="searching", driver_id=None, service_area_id="area-child")
        child_area = {"id": "area-child", "subscription_required": True, "parent_service_area_id": "area-parent"}
        active_sub = {"id": "sub-1", "plan_id": "plan-1"}
        plan = {"id": "plan-1", "service_areas": ["area-parent"], "vehicle_types": None}
        patches = self._patches(
            ride,
            {"drivers": [_driver()], "driver_subscriptions": [active_sub]},
            {("service_areas", "area-child"): child_area, ("subscription_plans", "plan-1"): plan},
        )
        with _Patches(
            *patches,
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(Exception):
                # Passes the subscription gate; fails later at the atomic
                # claim (no update_one row match configured) -- proves the
                # 402 was NOT raised for this branch.
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

    async def test_plan_vehicle_type_mismatch_blocks(self):
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import SpinrException

        ride = _ride(status="searching", driver_id=None, service_area_id="area-1")
        area = {"id": "area-1", "subscription_required": True}
        active_sub = {"id": "sub-1", "plan_id": "plan-1"}
        plan = {"id": "plan-1", "service_areas": None, "vehicle_types": ["vt-suv"]}
        patches = self._patches(
            ride,
            {"drivers": [_driver(vehicle_type_id="vt-sedan")], "driver_subscriptions": [active_sub]},
            {("service_areas", "area-1"): area, ("subscription_plans", "plan-1"): plan},
        )
        with _Patches(*patches):
            with pytest.raises(SpinrException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 402

    async def test_subscription_check_db_error_fails_closed_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="searching", driver_id=None, service_area_id="area-1")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch(
                "backend.routes.drivers._deps.db_supabase.find_one",
                AsyncMock(side_effect=RuntimeError("service_areas lookup exploded")),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 503


class TestAcceptRideAssignmentAndClaim:
    async def test_searching_path_without_pending_offer_is_403(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="searching", driver_id=None, service_area_id=None)

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "ride_offers":
                return []
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 403

    async def test_ride_offers_lookup_exception_still_403(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="searching", driver_id=None, service_area_id=None)

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "ride_offers":
                raise RuntimeError("ride_offers query failed")
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 403

    async def test_not_assigned_and_not_searching_is_400(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="driver_arrived", driver_id="some-other-driver", service_area_id=None)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 400

    async def test_claim_lost_but_same_driver_won_concurrently_is_idempotent_success(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride = _ride(status="driver_assigned", driver_id=_DRIVER_ID, service_area_id=None)
        won_ride = _ride(status="driver_accepted", driver_id=_DRIVER_ID, service_area_id=None)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=won_ride)),
        ):
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True, "already_accepted": True}

    async def test_claim_lost_to_another_driver_raises_409(self):
        from backend.routes.drivers.ride_flow import accept_ride
        from backend.utils.error_handling import SpinrException

        ride = _ride(status="driver_assigned", driver_id=_DRIVER_ID, service_area_id=None)
        taken_ride = _ride(status="driver_accepted", driver_id="other-driver", service_area_id=None)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=taken_ride)),
        ):
            with pytest.raises(SpinrException) as exc:
                await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 409


class TestAcceptRideSuccessSideEffects:
    def _base_success_patches(self, ride_after_claim, *, run_sync_side_effect=None):
        return (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_ride",
                AsyncMock(return_value=_ride(status="driver_assigned", service_area_id=None)),
            ),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=ride_after_claim)),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.invalidate_active_rides_cache", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=run_sync_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
            patch("backend.routes.drivers._deps.send_live_activity_update", AsyncMock()),
        )

    async def test_batch_offer_winner_and_losers_resolved(self):
        from backend.routes.drivers.ride_flow import accept_ride

        offered_at = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat()
        winner_result = MagicMock(data=[{"offered_at": offered_at}])
        losers_result = MagicMock(data=[{"driver_id": "loser-1"}])
        preempt_result = MagicMock(data=[])

        ride_after_claim = _ride(status="driver_accepted", service_area_id=None)
        patches = self._base_success_patches(
            ride_after_claim,
            run_sync_side_effect=[winner_result, losers_result, preempt_result],
        )
        with _Patches(
            *patches,
            patch(
                "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value={"id": "loser-1", "user_id": "loser-user-1"}),
            ),
        ):
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_batch_offer_cleanup_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride_after_claim = _ride(status="driver_accepted", service_area_id=None)
        patches = self._base_success_patches(
            ride_after_claim, run_sync_side_effect=RuntimeError("ride_offers table down")
        )
        with _Patches(*patches):
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_ride_metrics_pickup_leg_written_on_success(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride_after_claim = _ride(
            status="driver_accepted",
            service_area_id=None,
            pickup_lat=52.1,
            pickup_lng=-106.6,
            ride_metrics={},
        )
        patches = self._base_success_patches(ride_after_claim, run_sync_side_effect=RuntimeError("no offers"))
        with _Patches(
            *patches,
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
        ) as mocks:
            db_upd = mocks[-1]
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        # Second update_one call (after the claim itself) is the ride_metrics
        # pickup-leg write -- filtered by id only, carrying the ride_metrics field.
        calls_with_metrics = [
            c for c in db_upd.await_args_list if c.args and c.args[0] == "rides" and "ride_metrics" in (c.args[2] or {})
        ]
        assert calls_with_metrics, "expected a ride_metrics pickup-leg write"

    async def test_ride_metrics_write_failure_is_non_fatal(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride_after_claim = _ride(
            status="driver_accepted",
            service_area_id=None,
            pickup_lat=52.1,
            pickup_lng=-106.6,
        )
        patches = self._base_success_patches(ride_after_claim, run_sync_side_effect=RuntimeError("no offers"))

        async def failing_update_one(table, filt, updates):
            if "ride_metrics" in (updates or {}):
                raise RuntimeError("ride_metrics write failed")
            return {"id": _RIDE_ID}

        with _Patches(
            *patches,
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(side_effect=failing_update_one)),
        ):
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_guest_booking_triggers_guest_notification(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride_after_claim = _ride(status="driver_accepted", service_area_id=None, guest_booking=True)
        patches = self._base_success_patches(ride_after_claim, run_sync_side_effect=RuntimeError("no offers"))
        with _Patches(
            *patches,
            patch("backend.services.guest_notification_service.notify_guest_driver_assigned", AsyncMock()),
        ) as mocks:
            guest_notify = mocks[-1]
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        guest_notify.assert_called_once()

    async def test_no_rider_id_skips_notification_block(self):
        from backend.routes.drivers.ride_flow import accept_ride

        ride_after_claim = _ride(status="driver_accepted", service_area_id=None, rider_id=None)
        patches = self._base_success_patches(ride_after_claim, run_sync_side_effect=RuntimeError("no offers"))
        with _Patches(
            *patches,
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        ) as mocks:
            send_msg = mocks[-1]
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        send_msg.assert_not_awaited()


# ============================================================
# decline_ride
# ============================================================


class TestDeclineRideGuards:
    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import decline_ride

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import decline_ride

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_409_when_ride_not_declinable_status(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="in_progress")
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(HTTPException) as exc:
                await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 409

    async def test_403_when_not_assigned_and_no_offer_claimed(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="searching", driver_id=None)
        offer_res = MagicMock(data=[])
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(return_value=offer_res)),
        ):
            with pytest.raises(HTTPException) as exc:
                await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 403


class TestDeclineRideSuccessBranches:
    def _base_patches(self, ride, *, run_sync_side_effect):
        return (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=run_sync_side_effect)),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.db.insert_one", AsyncMock()),
            patch("backend.utils.redis_client.redis_set", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        )

    async def test_audit_log_insert_failure_is_non_fatal(self):
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="driver_assigned")
        patches = self._base_patches(ride, run_sync_side_effect=RuntimeError("no offer row"))
        with _Patches(
            *patches,
            patch("backend.routes.drivers._deps.db.insert_one", AsyncMock(side_effect=RuntimeError("audit db down"))),
        ):
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_redis_cooldown_set_failure_is_non_fatal(self):
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="driver_assigned")
        patches = self._base_patches(ride, run_sync_side_effect=RuntimeError("no offer row"))
        with _Patches(
            *patches,
            patch("backend.utils.redis_client.redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_early_redispatch_when_no_offers_remain(self):
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="searching", driver_id=None)
        offer_res = MagicMock(data=[{"id": "offer-1"}])  # this driver's own offer, claimed
        fresh_ride = _ride(status="searching", driver_id=None)
        remaining_res = MagicMock(data=[])

        with _Patches(
            *self._base_patches(ride, run_sync_side_effect=[offer_res, remaining_res]),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, fresh_ride])),
            patch("backend.routes.rides.match_driver_to_ride", AsyncMock()),
        ) as mocks:
            rematch = mocks[-1]
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        rematch.assert_called_once()

    async def test_no_redispatch_when_offers_remain(self):
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="searching", driver_id=None)
        offer_res = MagicMock(data=[{"id": "offer-1"}])
        fresh_ride = _ride(status="searching", driver_id=None)
        remaining_res = MagicMock(data=[{"id": "offer-2"}])

        with _Patches(
            *self._base_patches(ride, run_sync_side_effect=[offer_res, remaining_res]),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, fresh_ride])),
            patch("backend.routes.rides.match_driver_to_ride", AsyncMock()),
        ) as mocks:
            rematch = mocks[-1]
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        rematch.assert_not_called()

    async def test_rematch_check_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="searching", driver_id=None)
        offer_res = MagicMock(data=[{"id": "offer-1"}])

        with _Patches(
            *self._base_patches(ride, run_sync_side_effect=[offer_res, RuntimeError("remaining lookup failed")]),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, ride])),
        ):
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_period_1_recorded_when_release_leaves_driver_available(self):
        """Insurance Period 2 opens at claim/offer time (matching.py); decline
        must close it back to Period 1 — but only when the driver is actually
        still online. Mirrors process_expired_offer's guard."""
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="driver_assigned")
        patches = list(self._base_patches(ride, run_sync_side_effect=RuntimeError("no offer row")))
        patches[4] = patch(
            "backend.routes.drivers._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"id": _DRIVER_ID, "is_available": True, "is_online": True}),
        )
        with _Patches(*patches) as mocks:
            period_transition = mocks[5]
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        period_transition.assert_awaited_once_with(_DRIVER_ID, 1)

    async def test_period_1_skipped_when_driver_went_offline_before_decline(self):
        """A driver who toggled offline between the offer being sent and this
        decline must NOT get a Period 1 row falsely reopened — they're
        already Period 0 from their own go-offline call."""
        from backend.routes.drivers.ride_flow import decline_ride

        ride = _ride(status="driver_assigned")
        patches = list(self._base_patches(ride, run_sync_side_effect=RuntimeError("no offer row")))
        patches[4] = patch(
            "backend.routes.drivers._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"id": _DRIVER_ID, "is_available": False, "is_online": False}),
        )
        with _Patches(*patches) as mocks:
            period_transition = mocks[5]
            result = await decline_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        period_transition.assert_not_awaited()


# ============================================================
# arrive_at_pickup
# ============================================================


class TestArriveAtPickupGuards:
    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import arrive_at_pickup

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import arrive_at_pickup

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            with pytest.raises(HTTPException) as exc:
                await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_400_when_outside_geofence(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import arrive_at_pickup

        far_driver = _driver(lat=53.5, lng=-108.0)  # far from pickup
        ride = _ride(status="driver_accepted", pickup_lat=52.1, pickup_lng=-106.6)

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [far_driver]
            return [ride]

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            with pytest.raises(HTTPException) as exc:
                await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 400
        assert "away from the pickup" in exc.value.detail

    async def test_within_radius_of_nav_point_when_pickup_pin_is_far(self):
        """A driver who followed navigation to a road-snapped point must not be
        rejected just because the rider's raw pin is > 200m away."""
        from backend.routes.drivers.ride_flow import arrive_at_pickup

        near_nav_driver = _driver(lat=52.1005, lng=-106.6005)
        ride = _ride(
            status="driver_accepted",
            pickup_lat=53.9,
            pickup_lng=-110.0,
            pickup_nav_lat=52.1,
            pickup_nav_lng=-106.6,
        )

        async def fake_get_rows(table, filters=None, **kw):
            return [near_nav_driver] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        ):
            result = await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_409_when_guard_none(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import arrive_at_pickup

        ride = _ride(status="driver_accepted")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 409

    async def test_guest_booking_sends_guest_arrival_sms(self):
        from backend.routes.drivers.ride_flow import arrive_at_pickup

        ride = _ride(status="driver_accepted", guest_booking=True)

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
            patch(
                "backend.services.guest_notification_service.notify_guest_driver_arrived", AsyncMock()
            ) as guest_notify,
        ):
            result = await arrive_at_pickup(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}
        guest_notify.assert_called_once()


# ============================================================
# verify_pickup_otp
# ============================================================


class TestVerifyPickupOtp:
    def _otp_req(self, otp="1234"):
        from backend.routes.drivers._shared import RideOTPRequest

        return RideOTPRequest(otp=otp)

    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import verify_pickup_otp

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await verify_pickup_otp(ride_id=_RIDE_ID, request=self._otp_req(), current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import verify_pickup_otp

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            with pytest.raises(HTTPException) as exc:
                await verify_pickup_otp(ride_id=_RIDE_ID, request=self._otp_req(), current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_400_when_otp_mismatch(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = _ride(status="driver_arrived", pickup_otp="9999")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            with pytest.raises(HTTPException) as exc:
                await verify_pickup_otp(ride_id=_RIDE_ID, request=self._otp_req("1234"), current_user={"id": _USER_ID})
        assert exc.value.status_code == 400

    async def test_409_when_guard_none(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = _ride(status="driver_arrived", pickup_otp="1234")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await verify_pickup_otp(ride_id=_RIDE_ID, request=self._otp_req("1234"), current_user={"id": _USER_ID})
        assert exc.value.status_code == 409

    async def test_success_transitions_and_notifies_rider(self):
        from backend.routes.drivers.ride_flow import verify_pickup_otp

        ride = _ride(status="driver_arrived", pickup_otp="1234")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()) as period,
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()) as send_msg,
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        ):
            result = await verify_pickup_otp(
                ride_id=_RIDE_ID, request=self._otp_req("1234"), current_user={"id": _USER_ID}
            )
        assert result == {"success": True}
        period.assert_awaited_once_with(_DRIVER_ID, 3, ride_id=_RIDE_ID)
        send_msg.assert_awaited_once()


# ============================================================
# start_ride (dev/staging no-OTP fallback)
# ============================================================


class TestStartRideProductionGuard:
    async def test_blocked_in_production_with_410(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import start_ride

        with patch("backend.core.config.settings") as mock_settings:
            mock_settings.ENV = "production"
            with pytest.raises(HTTPException) as exc:
                await start_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 410

    async def test_404_driver_not_found_outside_production(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import start_ride

        with (
            patch("backend.core.config.settings") as mock_settings,
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            mock_settings.ENV = "development"
            with pytest.raises(HTTPException) as exc:
                await start_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_409_when_guard_none_outside_production(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import start_ride

        ride = _ride(status="driver_arrived")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.core.config.settings") as mock_settings,
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
        ):
            mock_settings.ENV = "development"
            with pytest.raises(HTTPException) as exc:
                await start_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 409


# ============================================================
# routes/drivers/ride_cancel.py — cancel_ride
# ============================================================


def _starlette_request(body: bytes = b""):
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/rides/x/cancel",
        "headers": [(b"content-length", str(len(body)).encode())],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return SR(scope, receive)


class TestDriverCancelRideGuards:
    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import cancel_ride

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import cancel_ride

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_error_when_ride_already_in_progress(self):
        from backend.routes.drivers.ride_cancel import cancel_ride
        from backend.utils.error_handling import RideStateError

        ride = _ride(status="in_progress")
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(RideStateError):
                await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})


class TestDriverCancelRideBodyReasonParsing:
    """The cancellation_reason attribution is written as part of the same
    status-guarded `update_one` claim call (not a separate update_ride call)
    — see the `{**base_update, "cancellation_reason": ...}` payload in
    ride_cancel.py's cancel_ride."""

    def _base_patches(self, ride, cancelled, *, update_one_mock):
        return (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers._deps.db_supabase.update_one", update_one_mock),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        )

    async def test_json_body_reason_takes_precedence_over_query_reason(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted")
        cancelled = _ride(status="cancelled")
        req = _starlette_request(body=b'{"reason": "traffic jam"}')
        upd_one = AsyncMock(return_value=cancelled)

        with _Patches(*self._base_patches(ride, cancelled, update_one_mock=upd_one)):
            result = await cancel_ride(
                ride_id=_RIDE_ID, reason="query-reason", request=req, current_user={"id": _USER_ID}
            )
        assert result == {"success": True}
        _, _, payload = upd_one.await_args_list[0].args
        assert payload["cancellation_reason"] == "traffic jam"

    async def test_json_body_parse_failure_falls_back_to_query_reason(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted")
        cancelled = _ride(status="cancelled")
        req = _starlette_request(body=b"not json but has length")

        async def _broken_receive():
            raise RuntimeError("body read failed")

        req._receive = _broken_receive
        upd_one = AsyncMock(return_value=cancelled)

        with _Patches(*self._base_patches(ride, cancelled, update_one_mock=upd_one)):
            result = await cancel_ride(
                ride_id=_RIDE_ID, reason="query-reason", request=req, current_user={"id": _USER_ID}
            )
        assert result == {"success": True}
        _, _, payload = upd_one.await_args_list[0].args
        assert payload["cancellation_reason"] == "query-reason"

    async def test_pgrst204_falls_back_to_minimal_update(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted")
        cancelled = _ride(status="cancelled")

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=[RuntimeError("column cancelled_by does not exist (PGRST204)"), cancelled]),
            ) as upd_one,
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        ):
            result = await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert result == {"success": True}
        assert upd_one.await_count == 2


class TestDriverCancelRidePreauthAndBroadcast:
    def _base_patches(self, ride, cancelled):
        return (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=cancelled)),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        )

    async def test_preauth_hold_released_on_success(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted", payment_intent_id="pi_123", auth_status="authorized")
        cancelled = _ride(status="cancelled")

        with _Patches(
            *self._base_patches(ride, cancelled),
            patch("backend.routes.drivers._deps.cancel_authorization", AsyncMock(return_value=True)),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
        ) as mocks:
            cancel_auth = mocks[-2]
            result = await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert result == {"success": True}
        cancel_auth.assert_awaited_once()

    async def test_preauth_release_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted", payment_intent_id="pi_123", auth_status="fare_only")
        cancelled = _ride(status="cancelled")

        with _Patches(
            *self._base_patches(ride, cancelled),
            patch(
                "backend.routes.drivers._deps.cancel_authorization",
                AsyncMock(side_effect=RuntimeError("stripe down")),
            ),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
        ):
            result = await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_admin_broadcast_failure_is_non_fatal(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted")
        cancelled = _ride(status="cancelled")

        with _Patches(
            *self._base_patches(ride, cancelled),
            patch(
                "backend.routes.drivers._deps.manager.broadcast_to_admins",
                AsyncMock(side_effect=RuntimeError("admin ws down")),
            ),
        ):
            result = await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert result == {"success": True}

    async def test_scheduled_ride_broadcasts_is_scheduled_true(self):
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted", is_scheduled=True)
        cancelled = _ride(status="cancelled", is_scheduled=True)

        with _Patches(
            *self._base_patches(ride, cancelled),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        ) as mocks:
            bcast = mocks[-1]
            result = await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert result == {"success": True}
        assert bcast.await_args.kwargs["is_scheduled"] is True


# ============================================================
# routes/drivers/ride_cancel.py — mark_rider_noshow
# ============================================================


class TestMarkRiderNoshowGuards:
    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_403_when_not_assigned_driver(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        ride = _ride(status="driver_arrived", driver_id="someone-else")
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(HTTPException) as exc:
                await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 403

    async def test_error_when_not_driver_arrived_status(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow
        from backend.utils.error_handling import RideStateError

        ride = _ride(status="in_progress")
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(RideStateError):
                await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

    async def test_400_when_arrival_time_not_recorded(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        ride = _ride(status="driver_arrived", driver_arrived_at=None)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises(HTTPException) as exc:
                await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 400

    async def test_400_when_wait_time_not_elapsed(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        ride = _ride(status="driver_arrived", driver_arrived_at=arrived, service_area_id=None)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={"noshow_wait_seconds": 300})),
        ):
            with pytest.raises(HTTPException) as exc:
                await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 400
        assert "more seconds" in exc.value.detail


class TestMarkRiderNoshowSuccess:
    def _base_patches(self, ride, *, area=None, settings=None):
        arrived_dt = ride["driver_arrived_at"]
        return (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value=settings if settings is not None else {"noshow_wait_seconds": 300}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(return_value=area)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
        )

    async def test_full_success_charges_wallet_and_pays_driver(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived,
            service_area_id=None,
            payment_method="wallet",
        )
        wallet = {"id": "wallet-1", "balance": 100.0}

        with _Patches(
            *self._base_patches(ride),
            # service_area_id is None on this ride, so the area find_one call
            # inside mark_rider_noshow is skipped entirely -- this is the
            # rider-wallet find_one only.
            patch(
                "backend.routes.drivers._deps.db_supabase.find_one",
                AsyncMock(return_value=wallet),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.wallet_apply_delta",
                AsyncMock(return_value={"applied_delta": "-4.50"}),
            ),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
        ) as mocks:
            wallet_debit, pay_driver = mocks[-2], mocks[-1]
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

        assert result["success"] is True
        assert result["noshow_fee_total"] == 4.5
        wallet_debit.assert_awaited_once()
        pay_driver.assert_awaited_once()

    async def test_partial_wallet_collection_is_logged_not_fatal(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived,
            service_area_id=None,
            payment_method="wallet",
        )
        wallet = {"id": "wallet-1", "balance": 1.0}

        with _Patches(
            *self._base_patches(ride),
            patch(
                "backend.routes.drivers._deps.db_supabase.find_one",
                AsyncMock(return_value=wallet),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.wallet_apply_delta",
                AsyncMock(return_value={"applied_delta": "-1.00"}),
            ),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
        ) as mocks:
            pay_driver = mocks[-1]
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

        assert result["success"] is True
        pay_driver.assert_awaited_once()

    async def test_card_payment_method_skips_wallet_debit(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived,
            service_area_id=None,
            payment_method="card",
        )

        with _Patches(
            *self._base_patches(ride),
            patch("backend.routes.drivers._deps.db_supabase.wallet_apply_delta", AsyncMock()),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
        ) as mocks:
            wallet_debit, pay_driver = mocks[-2], mocks[-1]
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})

        assert result["success"] is True
        wallet_debit.assert_not_awaited()
        pay_driver.assert_awaited_once()

    async def test_area_override_wait_seconds_used(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived,
            service_area_id="area-1",
            payment_method="card",
        )
        area = {"id": "area-1", "noshow_wait_seconds": 10}

        with _Patches(
            *self._base_patches(ride, area=area),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
        ):
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result["success"] is True

    async def test_extended_fee_columns_write_failure_falls_back_to_minimal(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived,
            service_area_id=None,
            payment_method="card",
        )

        with _Patches(
            *self._base_patches(ride),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_ride",
                AsyncMock(side_effect=[RuntimeError("column does not exist (PGRST204)"), None]),
            ),
        ) as mocks:
            upd_ride = mocks[-1]
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result["success"] is True
        assert upd_ride.await_count == 2

    async def test_admin_broadcast_failure_is_non_fatal(self):
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived,
            service_area_id=None,
            payment_method="card",
        )

        with _Patches(
            *self._base_patches(ride),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
            patch(
                "backend.routes.drivers._deps.manager.broadcast_to_admins",
                AsyncMock(side_effect=RuntimeError("admin ws down")),
            ),
        ):
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result["success"] is True


# ============================================================
# routes/drivers/ride_cancel.py — rate_rider
# ============================================================


class TestRateRiderGuard:
    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_cancel import rate_rider
        from backend.schemas import RideRatingRequest

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await rate_rider(
                    ride_id=_RIDE_ID,
                    rating_data=RideRatingRequest(rating=5, comment=""),
                    current_user={"id": _USER_ID},
                )
        assert exc.value.status_code == 404


# ============================================================
# routes/drivers/ride_reads.py — get_active_ride
# ============================================================


def _chain(rows):
    """A MagicMock that chain-returns itself for select/eq/or_/limit and
    exposes `.execute` as a plain (not-yet-called) attribute returning
    MagicMock(data=rows) when invoked -- matches the
    `db_supabase.run_sync(qb.execute)` pattern (a bare callable reference
    is passed in, not an already-invoked result)."""
    m = MagicMock()
    m.select.return_value = m
    m.eq.return_value = m
    m.or_.return_value = m
    m.limit.return_value = m
    m.in_.return_value = m
    m.execute.return_value = MagicMock(data=rows)
    return m


def _raising_chain(exc):
    m = MagicMock()
    m.select.return_value = m
    m.eq.return_value = m
    m.or_.return_value = m
    m.limit.return_value = m
    m.in_.return_value = m
    m.execute.side_effect = exc
    return m


class TestGetActiveRideBatchOfferFallback:
    async def test_no_active_ride_and_no_pending_offer_returns_none(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        run_sync = AsyncMock(return_value=MagicMock(data=[]))
        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.run_sync", run_sync),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["ride"] is None

    async def test_pending_batch_offer_returns_ride_marked_driver_assigned(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        offer_ride = _ride(status="searching", driver_id=None, service_area_id=None)

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "vehicle_types":
                return [{"id": "vt-1", "name": "Sedan"}]
            return []

        run_sync = AsyncMock(return_value=MagicMock(data=[{"ride_id": _RIDE_ID}]))
        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.run_sync", run_sync),
            patch("backend.routes.drivers.ride_reads.db_supabase.get_ride", AsyncMock(return_value=offer_ride)),
            patch("backend.routes.drivers.ride_reads.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["ride"]["status"] == "driver_assigned"

    async def test_pending_offer_lookup_exception_returns_none(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        run_sync = AsyncMock(side_effect=RuntimeError("ride_offers query failed"))
        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.run_sync", run_sync),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["ride"] is None


class TestGetActiveRideEnrichment:
    def _base_patches(self, ride, *, rider_side_effect=None, vehicle_rows=None):
        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "vehicle_types":
                return vehicle_rows if vehicle_rows is not None else [{"id": "vt-1", "name": "Sedan"}]
            return []

        get_user_mock = (
            AsyncMock(side_effect=rider_side_effect)
            if rider_side_effect is not None
            else AsyncMock(return_value={"id": _RIDER_ID, "first_name": "Jamie", "rating": 4.9})
        )
        return (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.get_user_by_id", get_user_mock),
        )

    async def test_rider_lookup_exception_leaves_rider_none(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_accepted", service_area_id=None)
        with _Patches(*self._base_patches(ride, rider_side_effect=RuntimeError("users table down"))):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["rider"] is None
        assert result["ride"]["id"] == _RIDE_ID

    async def test_vehicle_type_lookup_exception_leaves_vehicle_type_none(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_accepted", service_area_id=None, vehicle_type_id="vt-1")

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "vehicle_types":
                raise RuntimeError("vehicle_types lookup failed")
            return []

        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["vehicle_type"] is None

    async def test_driver_assigned_status_includes_incentives_and_quest_hint(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_assigned", service_area_id=None, vehicle_type_id="vt-1")
        incentives = [{"name": "Rush hour bonus", "bonus_amount": 5.0, "incentive_type": "per_ride"}]
        quest_rows = [
            {
                "current_value": 3,
                "status": "active",
                "quest": {"title": "5 rides today", "target_value": 5, "reward_amount": 10},
            }
        ]
        fake_supabase = MagicMock()
        fake_supabase.table.side_effect = lambda name: (
            _chain(incentives) if name == "ride_incentives" else _chain(quest_rows)
        )

        with _Patches(
            *self._base_patches(ride),
            patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.run_sync",
                AsyncMock(side_effect=lambda fn: fn()),
            ),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})

        assert result["incentives"] == incentives
        assert result["total_bonus"] == 5.0
        assert result["quest_hint"]["title"] == "5 rides today"
        assert result["quest_hint"]["progress_pct"] == 60.0

    async def test_incentive_lookup_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_assigned", service_area_id=None, vehicle_type_id="vt-1")
        fake_supabase = MagicMock()
        fake_supabase.table.side_effect = lambda name: (
            _raising_chain(RuntimeError("ride_incentives down")) if name == "ride_incentives" else _chain([])
        )

        with _Patches(
            *self._base_patches(ride),
            patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.run_sync",
                AsyncMock(side_effect=lambda fn: fn()),
            ),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["incentives"] is None

    async def test_quest_hint_lookup_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_assigned", service_area_id=None, vehicle_type_id="vt-1")
        fake_supabase = MagicMock()
        fake_supabase.table.side_effect = lambda name: (
            _chain([]) if name == "ride_incentives" else _raising_chain(RuntimeError("quest_progress down"))
        )

        with _Patches(
            *self._base_patches(ride),
            patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.run_sync",
                AsyncMock(side_effect=lambda fn: fn()),
            ),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["quest_hint"] is None

    async def test_service_area_polygon_included_when_present(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_accepted", service_area_id="area-1")
        polygon = [{"lat": 52.1, "lng": -106.6}]

        with _Patches(
            *self._base_patches(ride),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.find_one",
                AsyncMock(return_value={"id": "area-1"}),
            ),
            patch("backend.routes.drivers.ride_reads.get_service_area_polygon", MagicMock(return_value=polygon)),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["service_area_polygon"] == polygon

    async def test_service_area_polygon_lookup_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_accepted", service_area_id="area-1")

        with _Patches(
            *self._base_patches(ride),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.find_one",
                AsyncMock(side_effect=RuntimeError("service_areas down")),
            ),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["service_area_polygon"] is None


# ============================================================
# routes/drivers/ride_reads.py — get_ride_history
# ============================================================


class TestGetRideHistoryEnrichment:
    async def test_incentive_claims_add_to_incentive_amount_and_total_earned(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(
            status="completed",
            base_fare=3.0,
            distance_fare=4.0,
            time_fare=1.0,
            tip_amount=2.0,
            cancellation_fee_driver=0,
            tax_amount=0.5,
        )
        claims_chain = _chain(None)
        claims_chain.execute.return_value = MagicMock(data=[{"ride_id": _RIDE_ID, "bonus_amount": 3.0}])
        fake_supabase = MagicMock()
        fake_supabase.table.return_value = claims_chain

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [completed]

        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
        ):
            result = await get_ride_history(limit=20, offset=0, current_user={"id": _USER_ID})

        row = result["rides"][0]
        assert row["incentive_amount"] == 3.0
        # fare_only = 3+4+1 = 8; total = 8 (fare) + 2 (tip) + 3 (incentive) + 0 (cancel) + 0.5 (tax)
        assert row["total_earned"] == 13.5

    async def test_incentive_claims_lookup_exception_is_non_fatal(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(status="completed")
        fake_supabase = MagicMock()
        fake_supabase.table.return_value = _raising_chain(RuntimeError("ride_incentive_claims down"))

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [completed]

        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
        ):
            result = await get_ride_history(limit=20, offset=0, current_user={"id": _USER_ID})
        assert result["total"] == 1

    async def test_driver_earnings_snapshot_present_uses_snapshot_values(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(
            status="completed",
            driver_earnings_snapshot={
                "total": 15.0,
                "fare": 8.0,
                "tip": 2.0,
                "incentive": 1.0,
                "cancel_fee": 0.0,
                "tax": 0.5,
            },
        )

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [completed]

        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(limit=20, offset=0, current_user={"id": _USER_ID})

        row = result["rides"][0]
        assert row["fare_only"] == 8.0
        assert row["incentive_amount"] == 1.0
        assert row["total_earned"] == 11.5

    async def test_fare_breakdown_snapshot_tax_fallback_when_tax_amount_zero(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(
            status="completed",
            base_fare=5.0,
            distance_fare=0,
            time_fare=0,
            tax_amount=0,
            fare_breakdown_snapshot={"lines": [{"type": "gst", "amount": 0.25}, {"type": "pst", "amount": 0.3}]},
        )

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [completed]

        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(limit=20, offset=0, current_user={"id": _USER_ID})

        row = result["rides"][0]
        assert row["tax_amount_total"] == 0.55

    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_reads import get_ride_history

        with patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_ride_history(limit=20, offset=0, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404

    async def test_period_and_explicit_status_filter_combined(self):
        """status='completed' + period='week' -> the isinstance(status_filter, str)
        branch (not the terminal-statuses dict/multi-query branch)."""
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(status="completed", ride_completed_at=datetime.now(timezone.utc).isoformat())
        captured = []

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            captured.append(filters)
            return [completed]

        with (
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(
                limit=20, offset=0, status="completed", period="week", current_user={"id": _USER_ID}
            )
        assert result["total"] == 1
        assert any("ride_completed_at" in f for f in captured)


# ============================================================
# Additional targeted gap-closers (found via real coverage --missing output)
# ============================================================


class TestAcceptRideLoserNotificationFailure:
    async def test_loser_ws_notify_failure_is_non_fatal(self):
        """A loser driver's ride_taken WS push failing must not block the
        winner's accept -- only logged (ride_flow.py's _release_loser)."""
        from backend.routes.drivers.ride_flow import accept_ride

        ride_after_claim = _ride(status="driver_accepted", service_area_id=None)
        winner_result = MagicMock(data=[])
        losers_result = MagicMock(data=[{"driver_id": "loser-1"}])
        preempt_result = MagicMock(data=[])

        patches = (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_ride",
                AsyncMock(return_value=_ride(status="driver_assigned", service_area_id=None)),
            ),
            patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=ride_after_claim)),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.invalidate_active_rides_cache", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.run_sync",
                AsyncMock(side_effect=[winner_result, losers_result, preempt_result]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value={"id": "loser-1", "user_id": "loser-user-1"}),
            ),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
            patch("backend.routes.drivers._deps.send_live_activity_update", AsyncMock()),
        )

        async def _send_personal_message(payload, recipient_key):
            # Only the LOSER's ride_taken push fails -- the winner's own
            # driver_accepted notification (a separate, unguarded call site)
            # must still succeed so this test isolates the loser-only branch.
            if recipient_key.startswith("driver_"):
                raise RuntimeError("ws send failed")

        with _Patches(
            *patches,
            patch(
                "backend.routes.drivers._deps.manager.send_personal_message",
                AsyncMock(side_effect=_send_personal_message),
            ),
        ):
            result = await accept_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result == {"success": True}


class TestStartRideRideNotFound:
    async def test_404_when_ride_not_found_outside_production(self):
        from fastapi import HTTPException

        from backend.routes.drivers.ride_flow import start_ride

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with (
            patch("backend.core.config.settings") as mock_settings,
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
        ):
            mock_settings.ENV = "development"
            with pytest.raises(HTTPException) as exc:
                await start_ride(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert exc.value.status_code == 404


class TestDriverCancelRidePreauthWriteFailure:
    async def test_auth_status_write_failure_after_release_is_non_fatal(self):
        """cancel_authorization succeeds but the follow-up auth_status=released
        write itself fails -- must not block the cancel response."""
        from backend.routes.drivers.ride_cancel import cancel_ride

        ride = _ride(status="driver_accepted", payment_intent_id="pi_123", auth_status="authorized")
        cancelled = _ride(status="cancelled")

        with _Patches(
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=cancelled)),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_ride",
                AsyncMock(side_effect=RuntimeError("auth_status write failed")),
            ),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
            patch("backend.routes.drivers._deps.cancel_authorization", AsyncMock(return_value=True)),
        ):
            result = await cancel_ride(ride_id=_RIDE_ID, reason="", request=None, current_user={"id": _USER_ID})
        assert result == {"success": True}


class TestMarkRiderNoshowNaiveDatetimeArrival:
    async def test_driver_arrived_at_as_naive_datetime_object_is_normalized(self):
        """driver_arrived_at stored as a real (non-str) datetime with no
        tzinfo -- the else-branch + tzinfo-is-None normalization."""
        from backend.routes.drivers.ride_cancel import mark_rider_noshow

        arrived_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=600)
        ride = _ride(
            status="driver_arrived",
            driver_arrived_at=arrived_naive,
            service_area_id=None,
            payment_method="card",
        )

        with _Patches(
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"noshow_wait_seconds": 300}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": _RIDE_ID})),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.spawn", side_effect=_spawn_close),
            patch("backend.services.cancellation_service.pay_driver_cancellation_fee", AsyncMock(return_value=True)),
        ):
            result = await mark_rider_noshow(ride_id=_RIDE_ID, current_user={"id": _USER_ID})
        assert result["success"] is True


class TestGetActiveRideBatchOfferRideNoLongerSearching:
    async def test_pending_offer_but_underlying_ride_no_longer_searching_returns_none(self):
        """A pending ride_offers row exists but the ride it points to has
        already moved past 'searching' (accepted by then) -- must fall
        through to no-active-ride, not surface a stale offer."""
        from backend.routes.drivers.ride_reads import get_active_ride

        already_accepted_ride = _ride(status="driver_accepted", service_area_id=None)

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        run_sync = AsyncMock(return_value=MagicMock(data=[{"ride_id": _RIDE_ID}]))
        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.run_sync", run_sync),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.get_ride",
                AsyncMock(return_value=already_accepted_ride),
            ),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["ride"] is None

    async def test_recent_rides_diag_lookup_exception_still_returns_none(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                # The initial active-ride lookup (limit=1, status filter) must
                # still succeed with no match; only the later limit=5
                # diagnostic "recent rides" query fails.
                if kw.get("limit") == 5:
                    raise RuntimeError("recent rides diag lookup failed")
                return []
            return []

        run_sync = AsyncMock(return_value=MagicMock(data=[]))
        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.run_sync", run_sync),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})
        assert result["ride"] is None


class TestGetActiveRideIncentiveAreaAndVehicleTypeFiltering:
    async def test_incentive_or_clause_applied_when_service_area_present(self):
        from backend.routes.drivers.ride_reads import get_active_ride

        ride = _ride(status="driver_assigned", service_area_id="area-1", vehicle_type_id="vt-1")
        incentive_matching = {"name": "Area bonus", "bonus_amount": 2.0, "incentive_type": "per_ride"}
        incentive_wrong_vt = {
            "name": "SUV bonus",
            "bonus_amount": 9.0,
            "incentive_type": "per_ride",
            "vehicle_type_id": "vt-suv",
        }
        fake_supabase = MagicMock()
        fake_supabase.table.side_effect = lambda name: (
            _chain([incentive_matching, incentive_wrong_vt]) if name == "ride_incentives" else _chain([])
        )

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "vehicle_types":
                return [{"id": "vt-1"}]
            return []

        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
            patch(
                "backend.routes.drivers.ride_reads.db_supabase.run_sync",
                AsyncMock(side_effect=lambda fn: fn()),
            ),
        ):
            result = await get_active_ride(current_user={"id": _USER_ID})

        # The mismatched-vehicle-type incentive is filtered out; only the
        # matching one contributes to the total bonus.
        assert result["total_bonus"] == 2.0
        assert len(result["incentives"]) == 1


class TestGetRideHistoryExplicitPeriodNone:
    async def test_explicit_period_none_short_circuits_history_start_for_period(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(status="completed")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [completed]

        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(limit=20, offset=0, period=None, current_user={"id": _USER_ID})
        assert result["total"] == 1

    async def test_explicit_period_all_short_circuits_history_start_for_period(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(status="completed")

        async def fake_get_rows(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [completed]

        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(limit=20, offset=0, period="all", current_user={"id": _USER_ID})
        assert result["total"] == 1

    async def test_month_period_with_dict_status_filter(self):
        """period='month' with no explicit status -> hits the month branch of
        history_start_for_period AND the terminal-statuses dict/multi-query
        path together."""
        from backend.routes.drivers.ride_reads import get_ride_history

        completed = _ride(status="completed", ride_completed_at=datetime.now(timezone.utc).isoformat())

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            return [completed]

        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(limit=20, offset=0, period="month", current_user={"id": _USER_ID})
        assert result["total"] >= 1

    async def test_explicit_scheduled_status_uses_scheduled_time_date_field(self):
        from backend.routes.drivers.ride_reads import get_ride_history

        scheduled = _ride(status="scheduled", scheduled_time=datetime.now(timezone.utc).isoformat())
        captured_orders = []

        async def fake_get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            captured_orders.append(kw.get("order"))
            return [scheduled]

        with _Patches(
            patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.drivers.ride_reads.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_ride_history(limit=20, offset=0, status="scheduled", current_user={"id": _USER_ID})
        assert result["total"] == 1
        # history_date_field("scheduled") -> "scheduled_time" is used as the
        # sort key -- confirms the SCHEDULED branch (not the default
        # "created_at" fallback) was taken.
        assert "scheduled_time" in captured_orders
