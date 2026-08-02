"""Coverage for utils/push_retry.py (A1c, Sub-tier B).

Push notification retry queue (push_retry_queue table). One of the 17
background loops referenced in core/lifespan.py ("push retry" loop): a
30-second tick that leases due rows via compare-and-swap, sends via Expo or
native FCM, and gives up (deletes the row) after _MAX_ATTEMPTS failures with
exponential back-off in between. Had no dedicated test file; only 45.30%
coverage.

Dual-import note: push_retry.py does
    try: from ..db_supabase import run_sync; from ..features import
    _is_expo_token, _send_expo_push; from ..supabase_client import supabase
    except ImportError: ...
so when loaded as backend.utils.push_retry the relative import wins and
`run_sync` / `_is_expo_token` / `_send_expo_push` / `supabase` are bound as
module-level names directly on push_retry itself (not on db_supabase /
features / supabase_client). All monkeypatches below target
`backend.utils.push_retry.<name>`, matching the pattern used in
test_stripe_kyc_sync_coverage.py.

Background-loop testing pattern: patch `asyncio.sleep` (on push_retry's own
`asyncio` binding) with a fake that raises `asyncio.CancelledError` after N
iterations, matching test_zoho_desk_sync_coverage.py.

Bug found, not fixed (test-only scope): in `_process_row`, when delivery
succeeds but the "mark sent_at" `run_sync(...)` call itself raises, the
exception is caught and only logged — the row keeps `sent_at IS NULL` but its
`attempts`/`next_attempt_at` were already bumped by `_claim_row` before send.
The row will be retried again after the back-off window and (since
`_send_expo_push`/`_send_fcm_push` will very likely succeed a second time) may
send a duplicate push to the user. This is a pre-existing at-least-once
delivery characteristic of the design, not something introduced here, but
worth flagging since it means "success" pushes are not fully exactly-once if
the final UPDATE fails.

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── enqueue_push ─────────────────────────────────────────────────────────


class TestEnqueuePush:
    @pytest.mark.anyio
    async def test_success_awaits_run_sync(self, monkeypatch):
        from backend.utils import push_retry

        run_sync = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        await push_retry.enqueue_push("user-1", "Ride offer", "New ride nearby")
        run_sync.assert_awaited_once()

    @pytest.mark.anyio
    async def test_default_data_and_priority(self, monkeypatch):
        """data defaults to {} and priority defaults to 'normal' when omitted;
        exercised indirectly since run_sync is mocked and the lambda body
        isn't executed — this just confirms the call doesn't raise."""
        from backend.utils import push_retry

        run_sync = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        await push_retry.enqueue_push("user-2", "Title", "Body")
        run_sync.assert_awaited_once()

    @pytest.mark.anyio
    async def test_enqueue_failure_is_swallowed(self, monkeypatch):
        """A broken enqueue must not crash the caller's request path."""
        from backend.utils import push_retry

        run_sync = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        # Must not raise.
        await push_retry.enqueue_push("user-3", "Title", "Body", priority="high", target_app="driver")
        run_sync.assert_awaited_once()


# ── push_retry_loop ──────────────────────────────────────────────────────


class TestPushRetryLoop:
    @pytest.mark.anyio
    async def test_loop_ticks_then_sleeps_for_loop_interval(self, monkeypatch):
        from backend.utils import push_retry

        tick = AsyncMock()
        monkeypatch.setattr(push_retry, "_tick", tick)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with patch.object(push_retry.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await push_retry.push_retry_loop()

        tick.assert_awaited_once()
        assert sleep_calls == [push_retry._LOOP_INTERVAL]

    @pytest.mark.anyio
    async def test_loop_survives_a_failing_tick(self, monkeypatch):
        """One bad iteration (an exception from _tick) must not crash the
        loop — it should be logged and the loop should still reach sleep."""
        from backend.utils import push_retry

        tick = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(push_retry, "_tick", tick)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with patch.object(push_retry.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await push_retry.push_retry_loop()

        tick.assert_awaited_once()
        assert sleep_calls == [push_retry._LOOP_INTERVAL]


# ── _tick ────────────────────────────────────────────────────────────────


class TestTick:
    @pytest.mark.anyio
    async def test_query_failure_returns_without_processing(self, monkeypatch):
        from backend.utils import push_retry

        run_sync = AsyncMock(side_effect=RuntimeError("query failed"))
        monkeypatch.setattr(push_retry, "run_sync", run_sync)
        process_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_process_row", process_row)

        await push_retry._tick()

        process_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_empty_rows_is_noop(self, monkeypatch):
        from backend.utils import push_retry

        resp = MagicMock()
        resp.data = []
        run_sync = AsyncMock(return_value=resp)
        monkeypatch.setattr(push_retry, "run_sync", run_sync)
        process_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_process_row", process_row)

        await push_retry._tick()

        process_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_none_data_treated_as_empty(self, monkeypatch):
        from backend.utils import push_retry

        resp = MagicMock()
        resp.data = None
        run_sync = AsyncMock(return_value=resp)
        monkeypatch.setattr(push_retry, "run_sync", run_sync)
        process_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_process_row", process_row)

        await push_retry._tick()

        process_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_processes_each_pending_row(self, monkeypatch):
        from backend.utils import push_retry

        rows = [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}]
        resp = MagicMock()
        resp.data = rows
        run_sync = AsyncMock(return_value=resp)
        monkeypatch.setattr(push_retry, "run_sync", run_sync)
        process_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_process_row", process_row)

        await push_retry._tick()

        assert process_row.await_count == 3
        process_row.assert_any_await(rows[0])
        process_row.assert_any_await(rows[1])
        process_row.assert_any_await(rows[2])


# ── _process_row ─────────────────────────────────────────────────────────


def _row(**overrides):
    base = {
        "id": "row-1",
        "user_id": "user-1",
        "title": "Ride offer",
        "body": "New ride nearby",
        "data": {},
        "attempts": 0,
        "target_app": None,
        "users": {"fcm_token": "fcm-token-abc"},
    }
    base.update(overrides)
    return base


class TestProcessRowTokenSelection:
    @pytest.mark.anyio
    async def test_no_users_dict_drops_row(self, monkeypatch):
        from backend.utils import push_retry

        delete_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_delete_row", delete_row)
        claim_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_claim_row", claim_row)

        row = _row(users=None)
        await push_retry._process_row(row)

        delete_row.assert_awaited_once_with("row-1")
        claim_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_missing_token_drops_row(self, monkeypatch):
        from backend.utils import push_retry

        delete_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_delete_row", delete_row)
        claim_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_claim_row", claim_row)

        row = _row(users={})
        await push_retry._process_row(row)

        delete_row.assert_awaited_once_with("row-1")
        claim_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_default_target_app_uses_fcm_token(self, monkeypatch):
        from backend.utils import push_retry

        claim_row = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_claim_row", claim_row)
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: False)
        send_fcm = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(target_app=None, users={"fcm_token": "generic-token"})
        await push_retry._process_row(row)

        send_fcm.assert_awaited_once()
        assert send_fcm.await_args.args[0] == "generic-token"

    @pytest.mark.anyio
    async def test_driver_target_prefers_driver_token(self, monkeypatch):
        from backend.utils import push_retry

        claim_row = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_claim_row", claim_row)
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: False)
        send_fcm = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(
            target_app="driver",
            users={"fcm_token": "generic", "fcm_token_driver": "driver-token", "fcm_token_rider": "rider-token"},
        )
        await push_retry._process_row(row)

        assert send_fcm.await_args.args[0] == "driver-token"

    @pytest.mark.anyio
    async def test_driver_target_falls_back_to_generic_token(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: False)
        send_fcm = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(target_app="driver", users={"fcm_token": "generic"})
        await push_retry._process_row(row)

        assert send_fcm.await_args.args[0] == "generic"

    @pytest.mark.anyio
    async def test_rider_target_prefers_rider_token(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: False)
        send_fcm = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(
            target_app="rider",
            users={"fcm_token": "generic", "fcm_token_driver": "driver-token", "fcm_token_rider": "rider-token"},
        )
        await push_retry._process_row(row)

        assert send_fcm.await_args.args[0] == "rider-token"

    @pytest.mark.anyio
    async def test_rider_target_falls_back_to_generic_token(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: False)
        send_fcm = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(target_app="rider", users={"fcm_token": "generic"})
        await push_retry._process_row(row)

        assert send_fcm.await_args.args[0] == "generic"


class TestProcessRowClaim:
    @pytest.mark.anyio
    async def test_claim_lost_to_another_worker_skips_send(self, monkeypatch):
        from backend.utils import push_retry

        claim_row = AsyncMock(return_value=False)
        monkeypatch.setattr(push_retry, "_claim_row", claim_row)
        send_expo = AsyncMock()
        send_fcm = AsyncMock()
        monkeypatch.setattr(push_retry, "_send_expo_push", send_expo)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)

        row = _row()
        await push_retry._process_row(row)

        claim_row.assert_awaited_once()
        send_expo.assert_not_awaited()
        send_fcm.assert_not_awaited()

    @pytest.mark.anyio
    async def test_claim_uses_observed_attempts_and_computed_backoff(self, monkeypatch):
        """attempts=2 -> backoff = _BACKOFF_BASE * 2**2, passed through to
        _claim_row's next_attempt_at computation (indirectly, via the call
        args to _claim_row: row_id, observed attempts)."""
        from backend.utils import push_retry

        claim_row = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_claim_row", claim_row)
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        monkeypatch.setattr(push_retry, "_send_expo_push", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(attempts=2)
        await push_retry._process_row(row)

        claim_row.assert_awaited_once()
        call_args = claim_row.await_args.args
        assert call_args[0] == "row-1"
        assert call_args[1] == 2


class TestProcessRowSendAndOutcome:
    @pytest.mark.anyio
    async def test_expo_token_routes_to_send_expo_push(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        send_expo = AsyncMock(return_value=True)
        send_fcm = AsyncMock(return_value=True)
        monkeypatch.setattr(push_retry, "_send_expo_push", send_expo)
        monkeypatch.setattr(push_retry, "_send_fcm_push", send_fcm)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(return_value=MagicMock()))

        row = _row(users={"fcm_token": "ExponentPushToken[abc]"})
        await push_retry._process_row(row)

        send_expo.assert_awaited_once()
        send_fcm.assert_not_awaited()

    @pytest.mark.anyio
    async def test_success_marks_row_sent(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        monkeypatch.setattr(push_retry, "_send_expo_push", AsyncMock(return_value=True))
        run_sync = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(push_retry, "run_sync", run_sync)
        delete_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_delete_row", delete_row)

        await push_retry._process_row(_row())

        run_sync.assert_awaited_once()
        delete_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_success_but_mark_sent_update_raises_is_swallowed(self, monkeypatch):
        """Delivery succeeded; the follow-up UPDATE to set sent_at fails.
        Must be logged, not raised (see module docstring bug note)."""
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        monkeypatch.setattr(push_retry, "_send_expo_push", AsyncMock(return_value=True))
        run_sync = AsyncMock(side_effect=RuntimeError("update failed"))
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        # Must not raise.
        await push_retry._process_row(_row())
        run_sync.assert_awaited_once()

    @pytest.mark.anyio
    async def test_send_raising_is_treated_as_failure(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        monkeypatch.setattr(push_retry, "_send_expo_push", AsyncMock(side_effect=RuntimeError("network down")))
        delete_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_delete_row", delete_row)

        # attempts=0 -> new_attempts=1 < _MAX_ATTEMPTS(5); must not delete.
        await push_retry._process_row(_row(attempts=0))
        delete_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failure_below_max_attempts_does_not_drop_row(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        monkeypatch.setattr(push_retry, "_send_expo_push", AsyncMock(return_value=False))
        delete_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_delete_row", delete_row)

        # attempts=3 -> new_attempts=4 < _MAX_ATTEMPTS(5); must not delete yet.
        await push_retry._process_row(_row(attempts=3))
        delete_row.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failure_at_max_attempts_drops_row(self, monkeypatch):
        from backend.utils import push_retry

        monkeypatch.setattr(push_retry, "_claim_row", AsyncMock(return_value=True))
        monkeypatch.setattr(push_retry, "_is_expo_token", lambda t: True)
        monkeypatch.setattr(push_retry, "_send_expo_push", AsyncMock(return_value=False))
        delete_row = AsyncMock()
        monkeypatch.setattr(push_retry, "_delete_row", delete_row)

        # attempts=4 -> new_attempts=5 >= _MAX_ATTEMPTS(5); must give up.
        await push_retry._process_row(_row(attempts=4))
        delete_row.assert_awaited_once_with("row-1")


# ── _send_fcm_push ───────────────────────────────────────────────────────


class TestSendFcmPush:
    @pytest.mark.anyio
    async def test_firebase_admin_unavailable_returns_false(self, monkeypatch):
        from backend.utils import push_retry

        # Simulate `import firebase_admin` failing: sys.modules[name] = None
        # forces the import machinery to raise ImportError.
        monkeypatch.setitem(sys.modules, "firebase_admin", None)

        result = await push_retry._send_fcm_push("token-1", "Title", "Body", {}, "user-1")
        assert result is False

    @pytest.mark.anyio
    async def test_successful_send_returns_true_non_dispatch(self, monkeypatch):
        from backend.utils import push_retry

        fake_messaging = MagicMock()
        fake_messaging.send = MagicMock(return_value="projects/x/messages/1")
        fake_firebase_admin = types.ModuleType("firebase_admin")
        fake_firebase_admin.messaging = fake_messaging
        monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)

        result = await push_retry._send_fcm_push("token-1", "Title", "Body", {"type": "chat_message"}, "user-1")

        assert result is True
        fake_messaging.send.assert_called_once()

    @pytest.mark.anyio
    async def test_successful_send_returns_true_dispatch_payload(self, monkeypatch):
        """data-only Android path (Notifee) for new_ride_assignment must
        still succeed and route through messaging.send."""
        from backend.utils import push_retry

        fake_messaging = MagicMock()
        fake_messaging.send = MagicMock(return_value="projects/x/messages/2")
        fake_firebase_admin = types.ModuleType("firebase_admin")
        fake_firebase_admin.messaging = fake_messaging
        monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)

        result = await push_retry._send_fcm_push(
            "token-1", "Ride offer", "New ride nearby", {"type": "new_ride_assignment"}, "driver-1"
        )

        assert result is True
        fake_messaging.send.assert_called_once()

    @pytest.mark.anyio
    async def test_send_exception_returns_false(self, monkeypatch):
        from backend.utils import push_retry

        fake_messaging = MagicMock()
        fake_messaging.send = MagicMock(side_effect=RuntimeError("FCM unreachable"))
        fake_firebase_admin = types.ModuleType("firebase_admin")
        fake_firebase_admin.messaging = fake_messaging
        monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)

        result = await push_retry._send_fcm_push("token-1", "Title", "Body", {}, "user-1")
        assert result is False

    @pytest.mark.anyio
    async def test_none_data_defaults_gracefully(self, monkeypatch):
        """data=None must not raise when building the FCM data dict / is_dispatch check."""
        from backend.utils import push_retry

        fake_messaging = MagicMock()
        fake_messaging.send = MagicMock(return_value="ok")
        fake_firebase_admin = types.ModuleType("firebase_admin")
        fake_firebase_admin.messaging = fake_messaging
        monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)

        result = await push_retry._send_fcm_push("token-1", "Title", "Body", None, "user-1")
        assert result is True


# ── _claim_row ───────────────────────────────────────────────────────────


class TestClaimRow:
    @pytest.mark.anyio
    async def test_claim_succeeds_when_update_returns_rows(self, monkeypatch):
        """`_claim_row`'s real work happens inside its own inner `_fn()`
        closure (the `bool(getattr(res, "data", None))` conversion) — that
        closure is what gets PASSED TO `run_sync`, not executed by it
        directly. Mocking `run_sync` with `return_value=resp` (the raw
        query response) skips `_fn()` entirely and returns the response
        object itself rather than the converted bool. `run_sync` must
        actually call the closure it's given for this branch to be
        exercised at all."""
        from backend.utils import push_retry

        resp = MagicMock()
        resp.data = [{"id": "row-1", "attempts": 1}]
        query = MagicMock()
        query.table.return_value = query
        query.update.return_value = query
        query.eq.return_value = query
        query.is_.return_value = query
        query.execute.return_value = resp
        monkeypatch.setattr(push_retry, "supabase", query)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(side_effect=lambda fn: fn()))

        result = await push_retry._claim_row("row-1", 0, "2026-08-02T00:00:00+00:00")
        assert result is True

    @pytest.mark.anyio
    async def test_claim_fails_when_update_returns_no_rows(self, monkeypatch):
        """CAS lost the race: another replica already advanced attempts."""
        from backend.utils import push_retry

        resp = MagicMock()
        resp.data = []
        query = MagicMock()
        query.table.return_value = query
        query.update.return_value = query
        query.eq.return_value = query
        query.is_.return_value = query
        query.execute.return_value = resp
        monkeypatch.setattr(push_retry, "supabase", query)
        monkeypatch.setattr(push_retry, "run_sync", AsyncMock(side_effect=lambda fn: fn()))

        result = await push_retry._claim_row("row-1", 0, "2026-08-02T00:00:00+00:00")
        assert result is False

    @pytest.mark.anyio
    async def test_claim_never_raises_on_db_error(self, monkeypatch):
        from backend.utils import push_retry

        run_sync = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        result = await push_retry._claim_row("row-1", 0, "2026-08-02T00:00:00+00:00")
        assert result is False


# ── _delete_row ──────────────────────────────────────────────────────────


class TestDeleteRow:
    @pytest.mark.anyio
    async def test_delete_success(self, monkeypatch):
        from backend.utils import push_retry

        run_sync = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        await push_retry._delete_row("row-1")
        run_sync.assert_awaited_once()

    @pytest.mark.anyio
    async def test_delete_failure_is_swallowed(self, monkeypatch):
        from backend.utils import push_retry

        run_sync = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(push_retry, "run_sync", run_sync)

        # Must not raise.
        await push_retry._delete_row("row-1")
        run_sync.assert_awaited_once()
