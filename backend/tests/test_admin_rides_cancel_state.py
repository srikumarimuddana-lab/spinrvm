"""State-machine guard tests for POST /api/admin/rides/{id}/cancel.

WS-1 subtask A (plans/2026-09-03-path-to-a-implementation-plan.md): the #1
Blocker from docs/audit/2026-09-03-engineering-director-teardown-round2.md
-- admin force-cancel previously accepted `in_progress` rides (only
`completed`/`cancelled` were rejected), letting an admin "cancel" a ride
with a passenger aboard without ever closing its Period 3 insurance window.

This file locks down:
  1. Only the pre-trip allowed set (_ADMIN_CANCELLABLE_STATUSES) can be
     cancelled -- `in_progress` (and any other non-pre-trip state) is
     rejected with 400.
  2. The write is a conditional update filtered on the status just read
     (mirrors routes/drivers/ride_flow.py:331's optimistic-lock pattern for
     driver-side accept) -- a concurrent status change between the read and
     the write loses the update_one() call (0 rows) and must surface as a
     409, never a silent 200.
  3. Freeing an assigned driver on a successful cancel records a Period 1
     (online, no ride) insurance-period transition row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_SUPER_ADMIN = {
    "id": "admin-001",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "rides", "earnings", "support"],
}

_DRIVER = {"id": "drv-1", "user_id": "drv-user-1", "name": "Dan Driver"}


def _ride(status: str, *, driver_id: str | None = None) -> dict:
    return {
        "id": "ride-1",
        "status": status,
        "rider_id": "usr-1",
        "driver_id": driver_id,
    }


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def as_super_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


class TestAdminCancelRideAllowedStates:
    @pytest.mark.parametrize(
        "status",
        ["scheduled", "searching", "driver_assigned", "driver_accepted", "driver_arrived"],
    )
    def test_pre_trip_states_are_cancellable(self, client, as_super_admin, status):
        ride = _ride(status)
        cancelled = {**ride, "status": "cancelled"}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.update_one", AsyncMock(return_value=cancelled)),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 200, f"status={status} should be cancellable"

    @pytest.mark.parametrize("status", ["in_progress", "completed", "cancelled"])
    def test_non_pre_trip_states_rejected_400(self, client, as_super_admin, status):
        """The #1 Blocker this subtask fixes: `in_progress` must be rejected
        exactly like the terminal states -- a ride with a passenger aboard
        cannot be force-cancelled, only force-completed (admin_complete_ride),
        so its insurance Period 3 closes correctly."""
        ride = _ride(status)
        update_one_mock = AsyncMock()
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.update_one", update_one_mock),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 400, f"status={status} must be rejected"
        update_one_mock.assert_not_awaited()


class TestAdminCancelRideRaceGuard:
    def test_concurrent_status_change_returns_409(self, client, as_super_admin):
        """Another request (a driver accepting, another admin action) changes
        the ride's status between our read and the conditional write. The
        update_one() filter {"id": ..., "status": <status just read>} then
        matches 0 rows and returns None -- must 409, not silently 200. The
        failure-path re-read confirms the ride really did move on."""
        ride = _ride("driver_assigned")
        raced = {**ride, "status": "driver_accepted"}
        with (
            patch("db_supabase.get_ride", AsyncMock(side_effect=[ride, raced])),
            patch("db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 409

    def test_zero_rows_with_unchanged_status_is_a_500_not_a_409(self, client, as_super_admin):
        """0 rows while the ride still holds the filtered status is a silently
        blocked write (RLS / service-role misconfiguration), not concurrency.
        Reporting it as 409 would make a broken deployment look like normal
        contention, so it stays a loud 500 -- the same signal this endpoint
        gave before the conditional update replaced the verify-by-re-read."""
        ride = _ride("driver_assigned")
        with (
            patch("db_supabase.get_ride", AsyncMock(side_effect=[ride, ride])),
            patch("db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 500

    def test_race_guard_filter_carries_status_just_read(self, client, as_super_admin):
        """The optimistic-lock filter must be scoped to the exact status this
        request observed, not just the ride id -- otherwise the conditional
        update provides no protection at all."""
        ride = _ride("driver_arrived")
        cancelled = {**ride, "status": "cancelled"}
        update_one_mock = AsyncMock(return_value=cancelled)
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.update_one", update_one_mock),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 200
        update_one_mock.assert_awaited_once()
        filters = update_one_mock.call_args.args[1]
        assert filters == {"id": "ride-1", "status": "driver_arrived"}


class TestAdminCancelRideInsurancePeriod:
    def test_driver_arrived_cancel_frees_driver_and_records_period_1(self, client, as_super_admin):
        ride = _ride("driver_arrived", driver_id="drv-1")
        cancelled = {**ride, "status": "cancelled"}
        record_period_mock = AsyncMock()
        # set_driver_available returns the post-write driver row; is_available
        # True is what authorises the Period-1 (online, no ride) write.
        set_avail_mock = AsyncMock(return_value={"id": "drv-1", "is_available": True})
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.update_one", AsyncMock(return_value=cancelled)),
            patch("db_supabase.set_driver_available", set_avail_mock),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("routes.admin.rides.record_period_transition", record_period_mock),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 200
        set_avail_mock.assert_awaited_once_with("drv-1", True)
        record_period_mock.assert_awaited_once_with("drv-1", 1)

    def test_no_period_1_when_release_clamped_offline(self, client, as_super_admin):
        """set_driver_available clamps is_available to False for a driver who
        went offline (or was suspended) while assigned. Their go-offline
        already logged Period 0, so opening Period 1 here would falsely reopen
        a commercial-insurance window for a driver on personal auto only."""
        ride = _ride("driver_assigned", driver_id="drv-1")
        cancelled = {**ride, "status": "cancelled"}
        record_period_mock = AsyncMock()
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.update_one", AsyncMock(return_value=cancelled)),
            patch("db_supabase.set_driver_available", AsyncMock(return_value={"id": "drv-1", "is_available": False})),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("routes.admin.rides.record_period_transition", record_period_mock),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            resp = client.post(f"/api/admin/rides/{ride['id']}/cancel", json={"reason": "test"})
        assert resp.status_code == 200
        record_period_mock.assert_not_awaited()

    def test_driver_freed_is_the_one_the_write_saw_not_the_stale_read(self, client, as_super_admin):
        """cancel_filter pins the status but not the driver, so a concurrent
        offer-timeout revert + re-claim on another replica can hand the ride to
        a different driver between our read and our write. The driver we free
        (and open Period 1 for) must be the one on the row the conditional
        update actually returned, not the stale one from the initial read --
        otherwise the real holder stays stuck is_available=False until the
        orphan-claim reaper, and a bogus Period 1 opens for someone else."""
        stale_ride = _ride("driver_assigned", driver_id="drv-STALE")
        written_ride = {**stale_ride, "status": "cancelled", "driver_id": "drv-REAL"}
        set_avail_mock = AsyncMock(return_value={"id": "drv-REAL", "is_available": True})
        record_period_mock = AsyncMock()
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=stale_ride)),
            patch("db_supabase.update_one", AsyncMock(return_value=written_ride)),
            patch("db_supabase.set_driver_available", set_avail_mock),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("routes.admin.rides.record_period_transition", record_period_mock),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            resp = client.post("/api/admin/rides/ride-1/cancel", json={"reason": "test"})
        assert resp.status_code == 200
        set_avail_mock.assert_awaited_once_with("drv-REAL", True)
        record_period_mock.assert_awaited_once_with("drv-REAL", 1)
