"""Coverage for utils/scheduled_rides.py (A1c, Sub-tier B).

Background loop (one of the 17 in `core/lifespan.py`) that flips scheduled
rides from ``scheduled`` -> ``searching`` at their dispatch time and sends
10-minute reminder pushes. Had no dedicated test file; only 55.40% coverage.

The scheduled->searching transition is an atomic DB claim (``db.update_one``
filtered on ``status='scheduled'``) per the CLAUDE.md race-guard convention:
zero rows returned means another replica/tick already won the claim (or the
ride moved on) and the function must return without acting or double
dispatching. A mandatory WS event (``manager.broadcast_ride_status``) is
emitted on a successful claim.

Background-loop testing pattern: patch `asyncio.sleep` (on the module under
test) with a fake that raises `asyncio.CancelledError` after N iterations,
matching test_zoho_desk_sync_coverage.py's convention.

Dual-import care: `_dispatch_scheduled_ride` resolves its collaborators via
three *different* dynamic imports, each with its own try/except order:
  - matching:   tries bare `routes.rides.matching` FIRST, `..routes.rides`
                (i.e. `backend.routes.rides.matching`) as fallback.
  - booking:    tries relative `..routes.rides.booking`
                (`backend.routes.rides.booking`) FIRST, bare as fallback.
  - monitoring: tries relative `..routes.admin.monitoring`
                (`backend.routes.admin.monitoring`) FIRST, bare as fallback.
Because both the backend package root and the plain `backend/` directory
sit on sys.path in this test env, the bare and `backend.`-qualified forms
of `routes.*` are genuinely different module objects (per session
convention). Every collaborator is therefore patched on BOTH the bare and
`backend.`-qualified module objects so the test doesn't silently depend on
import-resolution order it can't observe without running the code.

Bug found, not fixed (test-only scope): `_dispatch_scheduled_ride`'s outer
`except Exception as e: logger.error(f"Failed to dispatch scheduled ride
{ride_id}: {e}", ...)` re-raises nothing and returns None either way, so a
non-unique-constraint failure from the `db.update_one` claim call (e.g. a
transient DB outage) is logged but otherwise silently absorbed at the
call site (`check_scheduled_rides`'s per-ride loop just moves to the next
ride). This matches the documented CLAUDE.md "do not silently swallow
errors" concern for DB paths, though `exc_info=True` does at least
preserve the traceback in logs. Flagging for follow-up, not fixing here.

Test-only change - no application code modified.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


def _import_both(dotted_bare: str, dotted_backend: str):
    """Best-effort import of both the bare and backend-qualified forms of a
    module. Returns (bare_module_or_None, backend_module_or_None)."""
    bare = None
    qualified = None
    try:
        bare = importlib.import_module(dotted_bare)
    except ImportError:
        bare = None
    try:
        qualified = importlib.import_module(dotted_backend)
    except ImportError:
        qualified = None
    return bare, qualified


def _patch_attr_everywhere(monkeypatch, modules, attr, value):
    for mod in modules:
        if mod is not None:
            monkeypatch.setattr(mod, attr, value, raising=False)


@pytest.fixture
def sr(monkeypatch):
    """The scheduled_rides module under test, with its `db` binding (the
    db_supabase module, per the `db.py` compat shim) ready to patch."""
    from backend.utils import scheduled_rides

    return scheduled_rides


@pytest.fixture
def matching_modules():
    return _import_both("routes.rides.matching", "backend.routes.rides.matching")


@pytest.fixture
def booking_modules():
    return _import_both("routes.rides.booking", "backend.routes.rides.booking")


@pytest.fixture
def monitoring_modules():
    return _import_both("routes.admin.monitoring", "backend.routes.admin.monitoring")


# ── _notify_schedule_delayed ────────────────────────────────────────────────


class TestNotifyScheduleDelayed:
    @pytest.mark.anyio
    async def test_no_rider_id_is_noop(self, sr, monkeypatch):
        redis_set_nx = AsyncMock()
        send_push = AsyncMock()
        monkeypatch.setattr(sr, "redis_set_nx", redis_set_nx)
        monkeypatch.setattr(sr, "send_push_notification", send_push)

        await sr._notify_schedule_delayed("ride-1", None, {})

        redis_set_nx.assert_not_awaited()
        send_push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_dedup_key_already_set_skips_notification(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=False))
        send_push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)

        await sr._notify_schedule_delayed("ride-1", "rider-1", {})

        send_push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_dedup_key_first_caller_sends_notification(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        send_push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)

        await sr._notify_schedule_delayed("ride-1", "rider-1", {})

        send_push.assert_awaited_once()
        args, kwargs = send_push.await_args
        assert args[0] == "rider-1"
        assert kwargs["data"]["ride_id"] == "ride-1"

    @pytest.mark.anyio
    async def test_redis_unavailable_falls_through_and_still_notifies(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down")))
        send_push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)

        await sr._notify_schedule_delayed("ride-1", "rider-1", {})

        send_push.assert_awaited_once()

    @pytest.mark.anyio
    async def test_push_failure_is_swallowed_best_effort(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down")))

        # Must not raise.
        await sr._notify_schedule_delayed("ride-1", "rider-1", {})


# ── _dispatch_scheduled_ride ─────────────────────────────────────────────────


def _ride(**overrides):
    base = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "payment_method": "cash",
        "dropoff_address": "123 Main St",
    }
    base.update(overrides)
    return base


class TestDispatchScheduledRide:
    @pytest.mark.anyio
    async def test_race_lost_claim_returns_no_row_does_nothing_further(self, sr, monkeypatch):
        """update_one filters on status='scheduled'; 0 rows (falsy return)
        means another replica/tick already won the claim."""
        update_one = AsyncMock(return_value=None)
        monkeypatch.setattr(sr.db, "update_one", update_one)
        broadcast = AsyncMock()
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", broadcast)

        await sr._dispatch_scheduled_ride(_ride())

        update_one.assert_awaited_once()
        broadcast.assert_not_awaited()

    @pytest.mark.anyio
    async def test_unique_violation_defers_ride_and_notifies_rider(self, sr, monkeypatch):
        """rides_one_active_per_rider collision: rider already has a live
        trip when the scheduled pickup arrives. Ride stays 'scheduled' for
        retry; rider is notified once (best-effort, Redis-deduped)."""
        monkeypatch.setattr(
            sr.db,
            "update_one",
            AsyncMock(
                side_effect=Exception('duplicate key value violates unique constraint "rides_one_active_per_rider"')
            ),
        )
        notify = AsyncMock()
        monkeypatch.setattr(sr, "_notify_schedule_delayed", notify)
        broadcast = AsyncMock()
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", broadcast)

        ride = _ride()
        await sr._dispatch_scheduled_ride(ride)

        notify.assert_awaited_once_with("ride-1", "rider-1", ride)
        broadcast.assert_not_awaited()

    @pytest.mark.anyio
    async def test_unique_violation_matched_via_23505_code(self, sr, monkeypatch):
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(side_effect=Exception("error 23505")))
        notify = AsyncMock()
        monkeypatch.setattr(sr, "_notify_schedule_delayed", notify)

        await sr._dispatch_scheduled_ride(_ride())

        notify.assert_awaited_once()

    @pytest.mark.anyio
    async def test_non_constraint_claim_error_is_logged_not_raised(self, sr, monkeypatch, caplog):
        """A claim failure unrelated to the active-ride unique constraint
        re-raises internally but is caught by the function's outer handler
        and logged loudly rather than propagating or being silently dropped."""
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(side_effect=RuntimeError("connection reset")))
        notify = AsyncMock()
        monkeypatch.setattr(sr, "_notify_schedule_delayed", notify)

        with caplog.at_level("ERROR"):
            # Must not raise.
            await sr._dispatch_scheduled_ride(_ride())

        notify.assert_not_awaited()
        assert any("Failed to dispatch scheduled ride" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_happy_path_cash_ride_dispatches_and_matches_driver(
        self, sr, monkeypatch, matching_modules, monitoring_modules
    ):
        claimed = _ride(payment_method="cash")
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=claimed))
        monkeypatch.setattr(sr.db, "get_user_by_id", AsyncMock(return_value={"id": "rider-1"}))
        broadcast_ride = AsyncMock()
        broadcast_admins = AsyncMock()
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", broadcast_ride)
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", broadcast_admins)

        for mod in monitoring_modules:
            _patch_attr_everywhere(
                monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {"id": ride["id"]}
            )

        match_driver = AsyncMock()
        search_timeout = AsyncMock()
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", match_driver)
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", search_timeout)

        send_push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)

        await sr._dispatch_scheduled_ride(_ride(payment_method="cash"))
        # Let the fire-and-forget ride_search_timeout task run.
        await asyncio.sleep(0)

        broadcast_ride.assert_awaited_once()
        call_kwargs = broadcast_ride.await_args
        assert call_kwargs.args[0] == "ride-1"
        assert call_kwargs.args[1] == "searching"
        assert call_kwargs.kwargs.get("rider_id") == "rider-1"
        assert call_kwargs.kwargs.get("is_scheduled") is True

        broadcast_admins.assert_awaited_once()
        admin_payload = broadcast_admins.await_args.args[0]
        assert admin_payload["type"] == "ride_requested"

        match_driver.assert_awaited_once_with("ride-1")
        search_timeout.assert_awaited_once_with("ride-1")

        send_push.assert_awaited_once()
        push_args, push_kwargs = send_push.await_args
        assert push_args[0] == "rider-1"
        assert push_kwargs["data"]["type"] == "scheduled_ride_dispatched"

    @pytest.mark.anyio
    async def test_no_rider_id_skips_final_push_notification(
        self, sr, monkeypatch, matching_modules, monitoring_modules
    ):
        claimed = _ride(rider_id=None, payment_method="cash")
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=claimed))
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", AsyncMock())
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", AsyncMock())
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", AsyncMock())
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", AsyncMock())
        for mod in monitoring_modules:
            _patch_attr_everywhere(monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {})
        send_push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)

        await sr._dispatch_scheduled_ride(_ride(rider_id=None, payment_method="cash"))
        await asyncio.sleep(0)

        send_push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_card_payment_preauthorizes_and_persists_returned_fields(
        self, sr, monkeypatch, matching_modules, booking_modules, monitoring_modules
    ):
        claimed = _ride(payment_method="card", auth_status=None, grand_total="12.50", payment_method_id="pm_1")
        update_one = AsyncMock(return_value=claimed)
        monkeypatch.setattr(sr.db, "update_one", update_one)
        monkeypatch.setattr(sr.db, "get_user_by_id", AsyncMock(return_value={"stripe_customer_id": "cus_1"}))
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", AsyncMock())
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", AsyncMock())
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", AsyncMock())
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", AsyncMock())
        for mod in monitoring_modules:
            _patch_attr_everywhere(monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {})
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        outcome = MagicMock()
        outcome.fields = {"auth_status": "authorized", "payment_intent_id": "pi_1"}
        preauth = AsyncMock(return_value=outcome)
        _patch_attr_everywhere(monkeypatch, booking_modules, "_preauthorize_ride_card", preauth)

        await sr._dispatch_scheduled_ride(_ride(payment_method="card", auth_status=None, grand_total="12.50"))
        await asyncio.sleep(0)

        preauth.assert_awaited_once()
        preauth_kwargs = preauth.await_args.kwargs
        assert preauth_kwargs["ride_id"] == "ride-1"
        assert preauth_kwargs["block_on_decline"] is False

        # Second update_one call persists the preauth outcome fields.
        assert update_one.await_count == 2
        second_call = update_one.await_args_list[1]
        assert second_call.args[0] == "rides"
        assert second_call.args[1] == {"id": "ride-1"}
        assert second_call.args[2] == {"$set": outcome.fields}

    @pytest.mark.anyio
    async def test_card_payment_with_existing_auth_status_skips_preauth(
        self, sr, monkeypatch, matching_modules, booking_modules, monitoring_modules
    ):
        claimed = _ride(payment_method="card", auth_status="authorized")
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=claimed))
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", AsyncMock())
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", AsyncMock())
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", AsyncMock())
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", AsyncMock())
        for mod in monitoring_modules:
            _patch_attr_everywhere(monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {})
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        preauth = AsyncMock()
        _patch_attr_everywhere(monkeypatch, booking_modules, "_preauthorize_ride_card", preauth)

        await sr._dispatch_scheduled_ride(_ride(payment_method="card", auth_status="authorized"))
        await asyncio.sleep(0)

        preauth.assert_not_awaited()

    @pytest.mark.anyio
    async def test_preauth_failure_does_not_block_dispatch(
        self, sr, monkeypatch, matching_modules, booking_modules, monitoring_modules
    ):
        """A pre-auth hiccup must not strand dispatch; post-trip settlement
        is the safety net, so the WS broadcast + matching must still fire."""
        claimed = _ride(payment_method="card", auth_status=None, grand_total="10.00")
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=claimed))
        monkeypatch.setattr(sr.db, "get_user_by_id", AsyncMock(return_value={}))
        broadcast_ride = AsyncMock()
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", broadcast_ride)
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", AsyncMock())
        match_driver = AsyncMock()
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", match_driver)
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", AsyncMock())
        for mod in monitoring_modules:
            _patch_attr_everywhere(monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {})
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        _patch_attr_everywhere(
            monkeypatch, booking_modules, "_preauthorize_ride_card", AsyncMock(side_effect=RuntimeError("stripe down"))
        )

        await sr._dispatch_scheduled_ride(_ride(payment_method="card", auth_status=None, grand_total="10.00"))
        await asyncio.sleep(0)

        broadcast_ride.assert_awaited_once()
        match_driver.assert_awaited_once()

    @pytest.mark.anyio
    async def test_ws_broadcast_failure_does_not_block_matching(
        self, sr, monkeypatch, matching_modules, monitoring_modules
    ):
        claimed = _ride(payment_method="cash")
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=claimed))
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", AsyncMock(side_effect=RuntimeError("ws down")))
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", AsyncMock())
        match_driver = AsyncMock()
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", match_driver)
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", AsyncMock())
        for mod in monitoring_modules:
            _patch_attr_everywhere(monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {})
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        await sr._dispatch_scheduled_ride(_ride(payment_method="cash"))
        await asyncio.sleep(0)

        match_driver.assert_awaited_once()

    @pytest.mark.anyio
    async def test_admin_broadcast_failure_does_not_block_matching(
        self, sr, monkeypatch, matching_modules, monitoring_modules
    ):
        claimed = _ride(payment_method="cash")
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=claimed))
        monkeypatch.setattr(sr.manager, "broadcast_ride_status", AsyncMock())
        monkeypatch.setattr(sr.manager, "broadcast_to_admins", AsyncMock(side_effect=RuntimeError("admin ws down")))
        monkeypatch.setattr(sr.db, "get_user_by_id", AsyncMock(return_value={}))
        match_driver = AsyncMock()
        _patch_attr_everywhere(monkeypatch, matching_modules, "match_driver_to_ride", match_driver)
        _patch_attr_everywhere(monkeypatch, matching_modules, "ride_search_timeout", AsyncMock())
        for mod in monitoring_modules:
            _patch_attr_everywhere(monkeypatch, [mod], "build_monitoring_ride", lambda ride, rider=None: {})
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        await sr._dispatch_scheduled_ride(_ride(payment_method="cash"))
        await asyncio.sleep(0)

        match_driver.assert_awaited_once()


# ── _send_reminder ───────────────────────────────────────────────────────────


class TestSendReminder:
    @pytest.mark.anyio
    async def test_already_reminded_is_noop(self, sr, monkeypatch):
        send_push = AsyncMock()
        update_one = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)
        monkeypatch.setattr(sr.db, "update_one", update_one)

        await sr._send_reminder(_ride(reminder_sent=True))

        send_push.assert_not_awaited()
        update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_sends_push_and_marks_reminder_sent(self, sr, monkeypatch):
        send_push = AsyncMock()
        update_one = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)
        monkeypatch.setattr(sr.db, "update_one", update_one)

        await sr._send_reminder(_ride())

        send_push.assert_awaited_once()
        push_args, push_kwargs = send_push.await_args
        assert push_args[0] == "rider-1"
        assert push_kwargs["data"]["type"] == "scheduled_ride_reminder"

        update_one.assert_awaited_once_with("rides", {"id": "ride-1"}, {"$set": {"reminder_sent": True}})

    @pytest.mark.anyio
    async def test_no_rider_id_still_marks_reminder_sent_without_push(self, sr, monkeypatch):
        send_push = AsyncMock()
        update_one = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", send_push)
        monkeypatch.setattr(sr.db, "update_one", update_one)

        await sr._send_reminder(_ride(rider_id=None))

        send_push.assert_not_awaited()
        update_one.assert_awaited_once()

    @pytest.mark.anyio
    async def test_db_error_is_logged_and_swallowed(self, sr, monkeypatch, caplog):
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(side_effect=RuntimeError("db down")))

        with caplog.at_level("ERROR"):
            await sr._send_reminder(_ride())

        assert any("Failed to send reminder" in r.message for r in caplog.records)


# ── check_scheduled_rides ────────────────────────────────────────────────────


class TestCheckScheduledRides:
    @pytest.mark.anyio
    async def test_lock_not_acquired_still_proceeds_to_fetch(self, sr, monkeypatch):
        """Unlike zoho's leader-election lock, this one is best-effort: the
        function only returns early if redis_set_nx returns False, but the
        rows it fetches after acquiring must still be processed if True."""
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=False))
        get_rows = AsyncMock()
        monkeypatch.setattr(sr.db, "get_rows", get_rows)

        await sr.check_scheduled_rides()

        get_rows.assert_not_awaited()

    @pytest.mark.anyio
    async def test_lock_redis_unavailable_proceeds_without_lock(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down")))
        get_rows = AsyncMock(return_value=[])
        monkeypatch.setattr(sr.db, "get_rows", get_rows)

        await sr.check_scheduled_rides()

        get_rows.assert_awaited_once()

    @pytest.mark.anyio
    async def test_get_rows_error_is_logged_and_returns(self, sr, monkeypatch, caplog):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(side_effect=RuntimeError("db down")))
        dispatch = AsyncMock()
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)

        with caplog.at_level("ERROR"):
            await sr.check_scheduled_rides()

        dispatch.assert_not_awaited()
        assert any("Failed to fetch scheduled rides" in r.message for r in caplog.records)

    # ── leader-lock self-lockout fix (2026-08-11 P1) ────────────────────────
    #
    # The TTL (90s) previously outlived the loop's ~54-66s jittered interval
    # and the lock was never explicitly released — the replica that won the
    # lock would fail to re-acquire its OWN still-live lock on the very next
    # tick, halving the real dispatch cadence to ~120s always, not just under
    # contention.

    @pytest.mark.anyio
    async def test_lock_is_released_after_a_successful_tick(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[]))
        redis_delete = AsyncMock()
        monkeypatch.setattr(sr, "redis_delete", redis_delete)

        result = await sr.check_scheduled_rides()

        assert result is True
        redis_delete.assert_awaited_once_with("spinr:scheduled_rides:lock")

    @pytest.mark.anyio
    async def test_lock_is_released_even_when_the_fetch_fails(self, sr, monkeypatch):
        """The lock must be freed on every exit path, not just the happy
        one -- otherwise a DB hiccup would strand the lock for its full TTL
        instead of freeing it immediately for the next tick/replica."""
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(side_effect=RuntimeError("db down")))
        redis_delete = AsyncMock()
        monkeypatch.setattr(sr, "redis_delete", redis_delete)

        result = await sr.check_scheduled_rides()

        assert result is False
        redis_delete.assert_awaited_once_with("spinr:scheduled_rides:lock")

    @pytest.mark.anyio
    async def test_lock_is_not_released_when_it_was_never_acquired(self, sr, monkeypatch):
        """When Redis itself is unavailable (redis_set_nx raises), there is
        no lock to release -- calling redis_delete anyway would be pointless
        and could mask a real problem in a future refactor that assumes
        `_holds_lock` tracking is meaningful."""
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down")))
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[]))
        redis_delete = AsyncMock()
        monkeypatch.setattr(sr, "redis_delete", redis_delete)

        await sr.check_scheduled_rides()

        redis_delete.assert_not_awaited()

    @pytest.mark.anyio
    async def test_lock_ttl_is_below_the_loop_interval(self, sr, monkeypatch):
        """The TTL is a crash-safety net, not the primary release mechanism
        -- it must stay below the loop's minimum jittered interval (60-6=54s)
        so a missed release (e.g. process killed mid-tick) self-heals within
        under one cycle, not two."""
        redis_set_nx = AsyncMock(return_value=True)
        monkeypatch.setattr(sr, "redis_set_nx", redis_set_nx)
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[]))
        monkeypatch.setattr(sr, "redis_delete", AsyncMock())

        await sr.check_scheduled_rides()

        _args, kwargs = redis_set_nx.await_args
        assert kwargs["ttl"] < 54, "lock TTL must stay below the loop's minimum jittered interval"

    @pytest.mark.anyio
    async def test_ride_missing_scheduled_time_is_skipped(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[{"id": "r1"}]))
        dispatch = AsyncMock()
        remind = AsyncMock()
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)
        monkeypatch.setattr(sr, "_send_reminder", remind)

        await sr.check_scheduled_rides()

        dispatch.assert_not_awaited()
        remind.assert_not_awaited()

    @pytest.mark.anyio
    async def test_unparsable_scheduled_time_is_skipped(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[{"id": "r1", "scheduled_time": "not-a-date"}]))
        monkeypatch.setattr(sr, "parse_iso_utc", lambda v: None)
        dispatch = AsyncMock()
        remind = AsyncMock()
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)
        monkeypatch.setattr(sr, "_send_reminder", remind)

        await sr.check_scheduled_rides()

        dispatch.assert_not_awaited()
        remind.assert_not_awaited()

    @pytest.mark.anyio
    async def test_within_reminder_window_and_not_yet_reminded_sends_reminder(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        near_future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        ride = {
            "id": "r1",
            "scheduled_time": near_future,
            "reminder_sent": False,
            "scheduled_dispatched": False,
        }
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[ride]))
        remind = AsyncMock()
        dispatch = AsyncMock()
        monkeypatch.setattr(sr, "_send_reminder", remind)
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)

        await sr.check_scheduled_rides()

        remind.assert_awaited_once_with(ride)
        dispatch.assert_not_awaited()  # not due yet (still in the future)

    @pytest.mark.anyio
    async def test_already_reminded_skips_reminder_even_in_window(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        near_future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        ride = {
            "id": "r1",
            "scheduled_time": near_future,
            "reminder_sent": True,
            "scheduled_dispatched": False,
        }
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[ride]))
        remind = AsyncMock()
        monkeypatch.setattr(sr, "_send_reminder", remind)
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", AsyncMock())

        await sr.check_scheduled_rides()

        remind.assert_not_awaited()

    @pytest.mark.anyio
    async def test_due_ride_not_yet_dispatched_is_dispatched(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        ride = {
            "id": "r1",
            "scheduled_time": past,
            "reminder_sent": True,
            "scheduled_dispatched": False,
        }
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[ride]))
        dispatch = AsyncMock()
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)
        monkeypatch.setattr(sr, "_send_reminder", AsyncMock())

        await sr.check_scheduled_rides()

        dispatch.assert_awaited_once_with(ride)

    @pytest.mark.anyio
    async def test_already_dispatched_ride_is_not_redispatched(self, sr, monkeypatch):
        """Guards against double-dispatch on a subsequent tick after this
        ride was already claimed and flipped to 'searching' (it would no
        longer even match the status='scheduled' query filter in practice,
        but the in-loop guard is defense in depth)."""
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        ride = {
            "id": "r1",
            "scheduled_time": past,
            "reminder_sent": True,
            "scheduled_dispatched": True,
        }
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[ride]))
        dispatch = AsyncMock()
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)
        monkeypatch.setattr(sr, "_send_reminder", AsyncMock())

        await sr.check_scheduled_rides()

        dispatch.assert_not_awaited()

    @pytest.mark.anyio
    async def test_multiple_rides_each_evaluated_independently(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        near_future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        due_ride = {"id": "r1", "scheduled_time": past, "reminder_sent": True, "scheduled_dispatched": False}
        reminder_ride = {
            "id": "r2",
            "scheduled_time": near_future,
            "reminder_sent": False,
            "scheduled_dispatched": False,
        }
        skip_ride = {"id": "r3"}  # no scheduled_time
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[due_ride, reminder_ride, skip_ride]))
        dispatch = AsyncMock()
        remind = AsyncMock()
        monkeypatch.setattr(sr, "_dispatch_scheduled_ride", dispatch)
        monkeypatch.setattr(sr, "_send_reminder", remind)

        await sr.check_scheduled_rides()

        dispatch.assert_awaited_once_with(due_ride)
        remind.assert_awaited_once_with(reminder_ride)


# ── scheduled_ride_dispatcher_loop ──────────────────────────────────────────


class TestScheduledRideDispatcherLoop:
    @pytest.mark.anyio
    async def test_happy_tick_calls_check_and_records_heartbeat_then_sleeps(self, sr, monkeypatch):
        check = AsyncMock()
        monkeypatch.setattr(sr, "check_scheduled_rides", check)
        heartbeat = MagicMock()
        monkeypatch.setattr(sr, "_record_heartbeat", heartbeat)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sr.scheduled_ride_dispatcher_loop()

        check.assert_awaited_once()
        heartbeat.assert_called_once_with("scheduled_dispatcher (60s)")
        assert len(sleep_calls) == 1
        # 60s +/- 6s jitter (B-P3-2).
        assert 54 <= sleep_calls[0] <= 66

    @pytest.mark.anyio
    async def test_check_error_is_logged_but_loop_still_reaches_sleep(self, sr, monkeypatch, caplog):
        monkeypatch.setattr(sr, "check_scheduled_rides", AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(sr, "_record_heartbeat", MagicMock())

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with caplog.at_level("ERROR"):
            with pytest.raises(asyncio.CancelledError):
                await sr.scheduled_ride_dispatcher_loop()

        assert sleep_calls  # loop survived the exception and reached sleep
        assert any("Scheduled ride dispatcher error" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_multiple_ticks_before_cancellation(self, sr, monkeypatch):
        check = AsyncMock()
        monkeypatch.setattr(sr, "check_scheduled_rides", check)
        monkeypatch.setattr(sr, "_record_heartbeat", MagicMock())

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sr.scheduled_ride_dispatcher_loop()

        assert check.await_count == 3
