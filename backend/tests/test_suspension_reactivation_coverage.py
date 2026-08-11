"""Coverage for utils/suspension_reactivation.py (A1c, Sub-tier B).

Background loop (one of the 17 in `core/lifespan.py`) that auto-flips
expired *temporary* rider suspensions (``suspended_until`` elapsed) back to
``status='active'``. Had no dedicated test file; only 55.93% coverage.

Replay-safety shape mirrors the CLAUDE.md background-loop conventions:
  - Redis leader lock (``redis_set_nx``) is a best-effort throttle, not a
    hard mutex — the loop still records a heartbeat and sleeps on a failed
    acquire, it just skips the tick's DB work for that interval.
  - The reactivation write is an ATOMIC conditional update filtered on
    ``status='suspended'``; a falsy/empty return means another replica (or
    an admin) already changed the row, so the audit insert is skipped to
    avoid a duplicate/misleading audit trail entry.
  - Indefinite suspensions (``suspended_until IS NULL``) never match the
    `$lte` filter and are therefore never touched by this loop, by
    construction of the query the test cannot exercise directly (it never
    reaches Postgres) but is documented for completeness.

Background-loop testing pattern: patch `asyncio.sleep` (as bound on the
module under test) with a fake that raises `asyncio.CancelledError` after N
iterations, matching test_zoho_desk_sync_coverage.py's convention.

Dual-import note: `suspension_reactivation.py` only has ONE try/except
import block (unlike scheduled_rides.py's three independent ones), and all
four collaborators (`db`, `send_push_notification`, `redis_set_nx`,
`_record_heartbeat`) are bound as plain module-level names either way. Since
this test always loads the module as `backend.utils.suspension_reactivation`,
the relative import arm wins and `sr.db`, `sr.send_push_notification`,
`sr.redis_set_nx`, `sr._record_heartbeat` are the correct (and only) patch
points — no bare-vs-qualified duplication needed here.

Driver invariant check (CLAUDE.md `is_available ⇒ is_online`): not
applicable — the `users` table this module reads has no role filter (a
suspended account can be a rider, a driver, or both via the `is_rider`/
`is_driver` dual-role flags); it never touches `is_online`/`is_available` at
all, so the driver online/available invariant has no bearing here.

N7/D21 (notification): `_reactivate_tick` now fires a best-effort
`send_push_notification(uid, ..., target_app=None)` call after a successful
reactivation, gated on the SAME "did our conditional update actually stick"
check as the audit-log insert above it — see `TestReactivationNotification`
below. `target_app=None` (legacy `fcm_token`) is a deliberate choice, not an
oversight: `users.role` is reserved for admin RBAC (migration
256_users_role_reject_admin_values.sql), not rider/driver discrimination, so
there is no single role signal to resolve a per-app token from — see the
module docstring for the full reasoning and the sibling admin-triggered code
path (`routes/admin/users.py`) it mirrors.

Bug found, not fixed (test-only scope): CLAUDE.md's background-loop and
state-machine sections both say "every state change must emit a WebSocket
event", but `_reactivate_tick` flips a live user's status from suspended to
active (and writes an audit_logs row) with no corresponding WS event to
that user's connection (if any) or to any admin-facing channel. A rider
whose suspension silently lifts mid-session while connected wouldn't see
their client state update until the next auth/refresh cycle notices the
new status server-side. Flagging for follow-up, not fixing here.

Test-only change - no application code modified.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def sr():
    from backend.utils import suspension_reactivation

    return suspension_reactivation


def _user(**overrides):
    base = {"id": "user-1", "suspended_until": "2026-01-01T00:00:00+00:00"}
    base.update(overrides)
    return base


# ── _pod_id ──────────────────────────────────────────────────────────────────


class TestPodId:
    def test_pod_id_combines_hostname_and_pid(self, sr, monkeypatch):
        monkeypatch.setattr(sr.socket, "gethostname", lambda: "host-a")
        monkeypatch.setattr(sr.os, "getpid", lambda: 4242)

        assert sr._pod_id() == "host-a:4242"


# ── _reactivate_tick ─────────────────────────────────────────────────────────


class TestReactivateTick:
    @pytest.mark.anyio
    async def test_fetch_error_is_logged_and_returns(self, sr, monkeypatch, caplog):
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(side_effect=RuntimeError("db down")))
        update_one = AsyncMock()
        monkeypatch.setattr(sr.db, "update_one", update_one)

        with caplog.at_level("ERROR"):
            # Must not raise.
            await sr._reactivate_tick()

        update_one.assert_not_awaited()
        assert any("failed to fetch candidates" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_no_candidates_returns_none_is_noop(self, sr, monkeypatch):
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=None))
        update_one = AsyncMock()
        monkeypatch.setattr(sr.db, "update_one", update_one)

        await sr._reactivate_tick()

        update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_empty_candidates_list_is_noop(self, sr, monkeypatch):
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[]))
        update_one = AsyncMock()
        monkeypatch.setattr(sr.db, "update_one", update_one)

        await sr._reactivate_tick()

        update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_candidate_missing_id_is_skipped(self, sr, monkeypatch):
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[{"suspended_until": "x"}]))
        update_one = AsyncMock()
        monkeypatch.setattr(sr.db, "update_one", update_one)

        await sr._reactivate_tick()

        update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_get_rows_called_with_expected_filter_shape(self, sr, monkeypatch):
        get_rows = AsyncMock(return_value=[])
        monkeypatch.setattr(sr.db, "get_rows", get_rows)

        await sr._reactivate_tick()

        get_rows.assert_awaited_once()
        args, kwargs = get_rows.await_args
        assert args[0] == "users"
        filter_dict = args[1]
        assert filter_dict["status"] == "suspended"
        assert "$lte" in filter_dict["suspended_until"]
        assert kwargs["limit"] == 500
        assert kwargs["columns"] == "id,suspended_until"

    @pytest.mark.anyio
    async def test_update_error_is_logged_and_next_candidate_still_processed(self, sr, monkeypatch, caplog):
        monkeypatch.setattr(
            sr.db,
            "get_rows",
            AsyncMock(return_value=[_user(id="user-1"), _user(id="user-2")]),
        )
        update_one = AsyncMock(side_effect=[RuntimeError("db down"), _user(id="user-2")])
        monkeypatch.setattr(sr.db, "update_one", update_one)
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        with caplog.at_level("ERROR"):
            await sr._reactivate_tick()

        assert update_one.await_count == 2
        # Only the second (successful) candidate reaches the audit insert...
        insert_one.assert_awaited_once()
        # ...and, same gate, only that candidate gets notified.
        push.assert_awaited_once()
        assert push.await_args.args[0] == "user-2"
        assert any("reactivate failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_race_lost_falsy_update_result_skips_audit(self, sr, monkeypatch):
        """update_one filters on status='suspended'; a falsy return (None or
        []) means another replica/admin already flipped this row — treat as
        'someone else won' and don't write a misleading audit entry."""
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[_user()]))
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=None))
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        await sr._reactivate_tick()

        insert_one.assert_not_awaited()
        # Race-loss must not notify either — same "did our update stick" gate.
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_race_lost_empty_list_update_result_skips_audit(self, sr, monkeypatch):
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[_user()]))
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=[]))
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        await sr._reactivate_tick()

        insert_one.assert_not_awaited()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_successful_update_calls_update_one_with_expected_fields(self, sr, monkeypatch):
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[_user(id="user-1")]))
        update_one = AsyncMock(return_value=_user(id="user-1"))
        monkeypatch.setattr(sr.db, "update_one", update_one)
        monkeypatch.setattr(sr.db, "insert_one", AsyncMock())
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        await sr._reactivate_tick()

        update_one.assert_awaited_once()
        args, kwargs = update_one.await_args
        assert args[0] == "users"
        assert args[1] == {"id": "user-1", "status": "suspended"}
        set_fields = args[2]
        assert set_fields["status"] == "active"
        assert set_fields["status_reason"] is None
        assert set_fields["suspended_until"] is None
        assert set_fields["status_changed_by"] == "system:auto_reactivation"
        assert "status_changed_at" in set_fields
        assert "updated_at" in set_fields

    @pytest.mark.anyio
    async def test_successful_update_writes_audit_log_with_expected_payload(self, sr, monkeypatch):
        monkeypatch.setattr(
            sr.db,
            "get_rows",
            AsyncMock(return_value=[_user(id="user-1", suspended_until="2026-01-01T00:00:00+00:00")]),
        )
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=_user(id="user-1")))
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock())

        await sr._reactivate_tick()

        insert_one.assert_awaited_once()
        args, _ = insert_one.await_args
        assert args[0] == "audit_logs"
        payload = args[1]
        assert payload["actor_id"] == "system"
        assert payload["actor_role"] == "system"
        assert payload["action"] == "user_status_change"
        assert payload["entity_type"] == "user"
        assert payload["entity_id"] == "user-1"
        assert payload["details"]["old_status"] == "suspended"
        assert payload["details"]["new_status"] == "active"
        assert payload["details"]["reason"] == "temporary suspension expired"
        assert payload["details"]["suspended_until"] == "2026-01-01T00:00:00+00:00"
        assert "id" in payload and "created_at" in payload

    @pytest.mark.anyio
    async def test_audit_insert_error_is_logged_as_warning_and_swallowed(self, sr, monkeypatch, caplog):
        """Per module docstring, only the DB flip needs to be loud; a failed
        audit insert after a successful reactivation is downgraded to a
        warning (the reactivation itself already succeeded) rather than
        surfaced as an error, and must not propagate."""
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[_user(id="user-1")]))
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=_user(id="user-1")))
        monkeypatch.setattr(sr.db, "insert_one", AsyncMock(side_effect=RuntimeError("audit db down")))
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        with caplog.at_level("WARNING"):
            # Must not raise.
            await sr._reactivate_tick()

        assert any("audit insert failed" in r.message for r in caplog.records)
        # The audit insert failing must not skip the notification that comes
        # after it — the reactivation itself (gated on `updated`) already
        # succeeded, independent of whether the audit trail write did.
        push.assert_awaited_once()
        assert push.await_args.args[0] == "user-1"

    @pytest.mark.anyio
    async def test_multiple_candidates_each_processed_independently(self, sr, monkeypatch):
        monkeypatch.setattr(
            sr.db,
            "get_rows",
            AsyncMock(return_value=[_user(id="user-1"), _user(id="user-2"), _user(id="user-3")]),
        )
        update_one = AsyncMock(
            side_effect=[
                _user(id="user-1"),  # succeeds -> audited
                None,  # race lost -> no audit
                _user(id="user-3"),  # succeeds -> audited
            ]
        )
        monkeypatch.setattr(sr.db, "update_one", update_one)
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        await sr._reactivate_tick()

        assert update_one.await_count == 3
        assert insert_one.await_count == 2
        audited_ids = {call.args[1]["entity_id"] for call in insert_one.await_args_list}
        assert audited_ids == {"user-1", "user-3"}
        # Same two winners get notified — the race-loser (user-2) gets neither.
        assert push.await_count == 2
        notified_ids = {call.args[0] for call in push.await_args_list}
        assert notified_ids == {"user-1", "user-3"}


# ── notification (N7 / D21) ─────────────────────────────────────────────────


class TestReactivationNotification:
    """`_reactivate_tick` now tells the user their suspension lifted, on top
    of the audit row it already wrote. Covers ACTION_ITEMS.md N7 (D21):
    'Auto-reactivation after suspended_until lapses is silent.'
    """

    @pytest.mark.anyio
    async def test_successful_reactivation_notifies_exactly_once_with_correct_shape(self, sr, monkeypatch):
        """(a) A successful reactivation fires exactly one push to the right
        uid, using send_push_notification's public kwarg contract."""
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[_user(id="user-1")]))
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=_user(id="user-1")))
        monkeypatch.setattr(sr.db, "insert_one", AsyncMock())
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        await sr._reactivate_tick()

        push.assert_awaited_once()
        args, kwargs = push.await_args
        # First positional arg is the recipient uid (send_push_notification's
        # own contract: user_id, title, body, ...).
        assert args[0] == "user-1"
        assert isinstance(args[1], str) and args[1]  # non-empty title
        assert isinstance(args[2], str) and args[2]  # non-empty body
        assert kwargs["data"] == {"type": "suspension_lifted"}
        assert kwargs["target_app"] is None

    @pytest.mark.anyio
    async def test_race_loss_never_notifies_even_when_candidate_looks_like_a_driver(self, sr, monkeypatch):
        """(b) Mirrors the audit-log guard exactly: a replica that loses the
        conditional update race must not notify, regardless of what the row
        looks like (here: a candidate that would plausibly be a driver)."""
        monkeypatch.setattr(
            sr.db,
            "get_rows",
            AsyncMock(return_value=[_user(id="driver-1", role="driver", is_driver=True, is_rider=False)]),
        )
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=None))
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        await sr._reactivate_tick()

        insert_one.assert_not_awaited()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_target_app_always_defaults_to_none_rider_and_driver_shaped_rows_alike(self, sr, monkeypatch):
        """(c) target_app resolution / fallback, decided explicitly: this
        module always passes target_app=None (legacy fcm_token) rather than
        resolving per-role, because `users.role` is admin-RBAC-only (not a
        rider/driver signal) and an account can be both rider and driver at
        once (is_rider/is_driver, migration 101). Proven for a row shaped
        like a driver candidate AND one shaped like a rider candidate — the
        outcome (None) must not depend on what's on the row."""
        driver_shaped = _user(id="driver-1", role="driver", is_driver=True, is_rider=False)
        rider_shaped = _user(id="rider-1", role="rider", is_driver=False, is_rider=True)
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[driver_shaped, rider_shaped]))
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(side_effect=[driver_shaped, rider_shaped]))
        monkeypatch.setattr(sr.db, "insert_one", AsyncMock())
        push = AsyncMock()
        monkeypatch.setattr(sr, "send_push_notification", push)

        await sr._reactivate_tick()

        assert push.await_count == 2
        for call in push.await_args_list:
            assert call.kwargs["target_app"] is None

    @pytest.mark.anyio
    async def test_notification_failure_does_not_raise_and_next_candidate_still_processed(self, sr, monkeypatch):
        """(d) A push failure for one candidate must not stop the loop from
        reaching the next one, and must not be able to retroactively affect
        the audit-log write that already happened before it (already-awaited
        insert_one calls stand regardless of what send_push_notification does
        afterwards)."""
        monkeypatch.setattr(
            sr.db,
            "get_rows",
            AsyncMock(return_value=[_user(id="user-1"), _user(id="user-2")]),
        )
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(side_effect=[_user(id="user-1"), _user(id="user-2")]))
        insert_one = AsyncMock()
        monkeypatch.setattr(sr.db, "insert_one", insert_one)
        push = AsyncMock(side_effect=[RuntimeError("fcm down"), None])
        monkeypatch.setattr(sr, "send_push_notification", push)

        # Must not raise, despite the first notification blowing up.
        await sr._reactivate_tick()

        # Both candidates were fully reactivated + audited, independent of
        # the first one's notification failure.
        assert insert_one.await_count == 2
        audited_ids = {call.args[1]["entity_id"] for call in insert_one.await_args_list}
        assert audited_ids == {"user-1", "user-2"}
        # The loop still attempted a notification for the second candidate
        # after the first one raised.
        assert push.await_count == 2

    @pytest.mark.anyio
    async def test_notification_failure_is_logged_as_warning_not_error(self, sr, monkeypatch, caplog):
        """Best-effort side-effect, not the core operation (mirrors the
        audit-insert-failure log level in the same function) — a push
        failure must not be logged at ERROR."""
        monkeypatch.setattr(sr.db, "get_rows", AsyncMock(return_value=[_user(id="user-1")]))
        monkeypatch.setattr(sr.db, "update_one", AsyncMock(return_value=_user(id="user-1")))
        monkeypatch.setattr(sr.db, "insert_one", AsyncMock())
        monkeypatch.setattr(sr, "send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down")))

        with caplog.at_level("WARNING"):
            await sr._reactivate_tick()

        assert any("notification failed" in r.message for r in caplog.records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)


# ── suspension_reactivation_loop ────────────────────────────────────────────


class TestSuspensionReactivationLoop:
    @pytest.mark.anyio
    async def test_lock_not_acquired_skips_tick_but_still_heartbeats_and_sleeps(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=False))
        tick = AsyncMock()
        monkeypatch.setattr(sr, "_reactivate_tick", tick)
        heartbeat = MagicMock()
        monkeypatch.setattr(sr, "_record_heartbeat", heartbeat)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sr.suspension_reactivation_loop()

        tick.assert_not_awaited()
        heartbeat.assert_called_once_with(sr._LOOP_NAME)
        assert sleep_calls == [sr.REACTIVATION_INTERVAL_SECONDS]

    @pytest.mark.anyio
    async def test_lock_acquired_runs_tick_heartbeats_and_sleeps_with_jitter(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        tick = AsyncMock()
        monkeypatch.setattr(sr, "_reactivate_tick", tick)
        heartbeat = MagicMock()
        monkeypatch.setattr(sr, "_record_heartbeat", heartbeat)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sr.suspension_reactivation_loop()

        tick.assert_awaited_once()
        heartbeat.assert_called_once_with(sr._LOOP_NAME)
        assert len(sleep_calls) == 1
        # interval +/- 10% jitter
        low = sr.REACTIVATION_INTERVAL_SECONDS * 0.9
        high = sr.REACTIVATION_INTERVAL_SECONDS * 1.1
        assert low <= sleep_calls[0] <= high

    @pytest.mark.anyio
    async def test_tick_error_is_logged_but_loop_still_heartbeats_and_sleeps(self, sr, monkeypatch, caplog):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(sr, "_reactivate_tick", AsyncMock(side_effect=RuntimeError("boom")))
        heartbeat = MagicMock()
        monkeypatch.setattr(sr, "_record_heartbeat", heartbeat)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with caplog.at_level("ERROR"):
            with pytest.raises(asyncio.CancelledError):
                await sr.suspension_reactivation_loop()

        heartbeat.assert_called_once_with(sr._LOOP_NAME)
        assert sleep_calls  # loop survived the exception and reached sleep
        assert any("Suspension reactivation loop error" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_redis_lock_error_is_not_swallowed_by_loop_body(self, sr, monkeypatch):
        """redis_set_nx itself raising is NOT inside the loop's try/except
        (only `_reactivate_tick()` is guarded) — this documents current
        behavior rather than asserting it's desirable."""
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down")))
        monkeypatch.setattr(sr, "_reactivate_tick", AsyncMock())
        monkeypatch.setattr(sr, "_record_heartbeat", MagicMock())
        monkeypatch.setattr(sr.asyncio, "sleep", AsyncMock())

        with pytest.raises(ConnectionError):
            await sr.suspension_reactivation_loop()

    @pytest.mark.anyio
    async def test_multiple_ticks_before_cancellation(self, sr, monkeypatch):
        monkeypatch.setattr(sr, "redis_set_nx", AsyncMock(return_value=True))
        tick = AsyncMock()
        monkeypatch.setattr(sr, "_reactivate_tick", tick)
        monkeypatch.setattr(sr, "_record_heartbeat", MagicMock())

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError()

        monkeypatch.setattr(sr.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sr.suspension_reactivation_loop()

        assert tick.await_count == 3
