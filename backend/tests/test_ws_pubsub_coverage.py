"""Coverage for utils/ws_pubsub.py (A1c, Sub-tier B).

WebSocket cross-replica fan-out over Redis pub/sub (the ``spinr:ws:dispatch``
channel referenced in CLAUDE.md's System Topology / "WebSocket event fan-out"
sections). Every Fly/Railway replica subscribes to one shared channel;
``publish``/``publish_broadcast``/``publish_kick_user`` push unicast,
prefix-broadcast, and control envelopes onto it, and the long-running
``_consumer`` loop delivers messages addressed to sockets local to this
replica. Had no dedicated test file; only 38.46% coverage (previously only
exercised indirectly via ``socket_manager``).

This file directly instantiates ``_WSPubSub`` (never the module-level
``pubsub`` singleton, to avoid cross-test state leakage) and covers:

- ``active``/``status()`` across all configured/connected/subscribed
  combinations.
- ``start()``: no-URL no-op, ``redis`` package ImportError, connect failure,
  subscribe failure, and the full success path.
- ``publish()``: inactive short-circuit, durable happy path (incr + buffered
  pipeline), incr failure falling back to an unwrapped bare publish, pipeline
  failure falling back to a bare publish, ``durable=False`` skipping the
  seq/outbox bookkeeping entirely, non-serialisable payloads, and a Redis
  error on the final bare publish.
- ``get_outbox()``: inactive, real-client happy path, and read failure
  (fail-open to ``[]``).
- ``publish_broadcast()`` / ``publish_kick_user()``: inactive, serialise
  failure, publish failure, success.
- ``_reconnect()``: success and failure branches.
- ``_consumer()``: every branch of the message-routing state machine
  (non-message frames, falsy frames, invalid JSON, control envelopes —
  known/unknown action and dispatch failure, unicast — missing client_id,
  delivery failure, and the "no kind" backwards-compat default, broadcast —
  empty prefix and delivery failure), plus the read-error/backoff/reconnect
  loop (below-threshold sleep, at-threshold reconnect success resetting the
  counter, and reconnect failure backing off) and immediate
  ``CancelledError`` propagation from ``get_message``.
- ``stop()`` and the ``_safe_close_pubsub``/``_safe_close_redis`` helpers.
- ``resolve_ws_redis_url()``.

Background-loop testing pattern (patching ``asyncio.sleep`` on the module
under test, scoped to a ``with patch.object(...)`` block) follows this
session's existing convention in ``test_zoho_desk_sync_coverage.py``. Patching
the real ``redis.asyncio`` import target follows the sys.modules pattern in
``test_redis_client_coverage.py`` (string-path ``patch("redis.asyncio.from_url",
...)`` is unreliable under full-suite ordering for this repo's redis package).

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _patch_redis_asyncio_module(monkeypatch, fake_aioredis):
    """Make `import redis.asyncio as redis_asyncio` resolve to ``fake_aioredis``.

    See test_redis_client_coverage.py's identical helper for the rationale:
    both the sys.modules entry and the attribute on the parent `redis`
    package must be patched together for `import a.b as x` to pick it up.
    """
    import redis as redis_pkg

    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_aioredis)
    monkeypatch.setattr(redis_pkg, "asyncio", fake_aioredis, raising=False)


def _active_instance(fake_redis, manager=None):
    """Build a ``_WSPubSub`` whose ``.active`` is True without going through
    ``start()`` — isolates publish/outbox/broadcast tests from connect logic."""
    from backend.utils import ws_pubsub

    inst = ws_pubsub._WSPubSub()
    inst._redis = fake_redis
    inst._pubsub = MagicMock()
    inst._task = MagicMock(done=MagicMock(return_value=False))
    inst._manager = manager or _fake_manager()
    inst._url = "redis://test-host:6379/0"
    return inst


def _fake_manager():
    manager = MagicMock()
    manager.disconnect_user = AsyncMock()
    manager._deliver_broadcast_local = AsyncMock()
    manager._deliver_local = AsyncMock()
    return manager


def _consumer_instance(manager=None):
    from backend.utils import ws_pubsub

    inst = ws_pubsub._WSPubSub()
    inst._pubsub = MagicMock()
    inst._manager = manager or _fake_manager()
    return inst


def _frame(data_dict):
    import json

    return {"type": "message", "data": json.dumps(data_dict)}


# ── active / status ──────────────────────────────────────────────────────


class TestActiveAndStatus:
    def test_active_false_when_nothing_set(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        assert inst.active is False

    def test_active_false_when_task_done(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        inst._redis = MagicMock()
        inst._pubsub = MagicMock()
        inst._task = MagicMock(done=MagicMock(return_value=True))
        assert inst.active is False

    def test_active_false_when_pubsub_missing_after_failed_reconnect(self):
        """Documented edge case: a failed _reconnect() nulls _pubsub but
        leaves _task alive — active must reflect that as False."""
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        inst._redis = MagicMock()
        inst._pubsub = None
        inst._task = MagicMock(done=MagicMock(return_value=False))
        assert inst.active is False

    def test_active_true_when_fully_connected(self):
        inst = _active_instance(MagicMock())
        assert inst.active is True

    def test_status_unconfigured(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        status = inst.status()
        assert status == {
            "active": False,
            "channel": ws_pubsub.CHANNEL,
            "backend_scheme": "",
            "configured": False,
            "last_error": None,
        }

    def test_status_configured_but_not_active_reports_last_error(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        inst._url = "redis://host:6379/0"
        inst._last_error = "connect: ConnectionError"
        status = inst.status()
        assert status["configured"] is True
        assert status["active"] is False
        assert status["backend_scheme"] == "redis"
        assert status["last_error"] == "connect: ConnectionError"

    def test_status_active_true(self):
        inst = _active_instance(MagicMock())
        status = inst.status()
        assert status["active"] is True
        assert status["configured"] is True


# ── start() ──────────────────────────────────────────────────────────────


class TestStart:
    @pytest.mark.anyio
    async def test_no_redis_url_is_noop(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        result = await inst.start(MagicMock(), "")
        assert result is False
        assert inst._redis is None
        assert inst._url == ""

    @pytest.mark.anyio
    async def test_redis_package_not_installed_returns_false(self, monkeypatch):
        from backend.utils import ws_pubsub

        monkeypatch.setitem(sys.modules, "redis.asyncio", None)
        inst = ws_pubsub._WSPubSub()
        result = await inst.start(MagicMock(), "redis://host:6379/0")
        assert result is False
        # self._url is only set AFTER the import succeeds.
        assert inst._url == ""

    @pytest.mark.anyio
    async def test_connect_failure_sets_last_error_and_returns_false(self, monkeypatch):
        from backend.utils import ws_pubsub

        fake_client = MagicMock()
        fake_client.ping = AsyncMock(side_effect=ConnectionError("refused"))
        fake_aioredis = MagicMock()
        fake_aioredis.from_url = MagicMock(return_value=fake_client)
        _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

        inst = ws_pubsub._WSPubSub()
        result = await inst.start(MagicMock(), "redis://host:6379/0")

        assert result is False
        assert inst._redis is None
        assert inst._url == "redis://host:6379/0"  # recorded before the connect attempt
        assert inst._last_error == "connect: ConnectionError"

    @pytest.mark.anyio
    async def test_subscribe_failure_closes_connections_and_returns_false(self, monkeypatch):
        from backend.utils import ws_pubsub

        fake_client = MagicMock()
        fake_client.ping = AsyncMock(return_value=True)
        fake_client.close = AsyncMock()
        fake_pubsub = MagicMock()
        fake_pubsub.subscribe = AsyncMock(side_effect=RuntimeError("subscribe failed"))
        fake_pubsub.unsubscribe = AsyncMock()
        fake_pubsub.close = AsyncMock()
        fake_client.pubsub = MagicMock(return_value=fake_pubsub)
        fake_aioredis = MagicMock()
        fake_aioredis.from_url = MagicMock(return_value=fake_client)
        _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

        inst = ws_pubsub._WSPubSub()
        result = await inst.start(MagicMock(), "redis://host:6379/0")

        assert result is False
        assert inst._redis is None
        assert inst._pubsub is None
        assert inst._last_error == "subscribe: RuntimeError"
        fake_client.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_success_starts_consumer_task(self, monkeypatch):
        from backend.utils import ws_pubsub

        fake_client = MagicMock()
        fake_client.ping = AsyncMock(return_value=True)
        fake_pubsub = MagicMock()
        fake_pubsub.subscribe = AsyncMock(return_value=None)
        fake_client.pubsub = MagicMock(return_value=fake_pubsub)
        fake_aioredis = MagicMock()
        fake_aioredis.from_url = MagicMock(return_value=fake_client)
        _patch_redis_asyncio_module(monkeypatch, fake_aioredis)

        inst = ws_pubsub._WSPubSub()
        monkeypatch.setattr(inst, "_consumer", AsyncMock())
        manager = MagicMock()

        result = await inst.start(manager, "redis://host:6379/0")

        assert result is True
        assert inst._redis is fake_client
        assert inst._pubsub is fake_pubsub
        assert inst._manager is manager
        assert inst._last_error is None
        assert inst._task is not None
        await inst._task  # let the mocked consumer coroutine finish


# ── publish() ────────────────────────────────────────────────────────────


class TestPublish:
    @pytest.mark.anyio
    async def test_inactive_returns_false(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        result = await inst.publish("rider_1", {"type": "x"})
        assert result is False

    @pytest.mark.anyio
    async def test_durable_happy_path_uses_pipeline(self):
        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock(return_value=5)
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[None, None, None, None])
        fake_redis.pipeline = MagicMock(return_value=pipe)
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish("rider_1", {"type": "x"}, durable=True)

        assert result is True
        fake_redis.incr.assert_awaited_once_with("spinr:ws:seq:rider_1")
        fake_redis.pipeline.assert_called_once_with(transaction=False)
        pipe.rpush.assert_called_once()
        pipe.ltrim.assert_called_once()
        pipe.expire.assert_called_once()
        pipe.publish.assert_called_once()
        pipe.execute.assert_awaited_once()
        fake_redis.publish.assert_not_awaited()  # bare publish skipped, pipeline already published

    @pytest.mark.anyio
    async def test_durable_incr_failure_falls_back_to_unwrapped_bare_publish(self):
        import json

        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock(side_effect=ConnectionError("down"))
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish("rider_1", {"type": "x"}, durable=True)

        assert result is True
        fake_redis.pipeline.assert_not_called()
        fake_redis.publish.assert_awaited_once()
        body = json.loads(fake_redis.publish.await_args.args[1])
        assert body["message"] == {"type": "x"}  # unwrapped, no seq envelope

    @pytest.mark.anyio
    async def test_durable_pipeline_failure_falls_back_to_bare_publish(self):
        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock(return_value=1)
        pipe = MagicMock()
        pipe.execute = AsyncMock(side_effect=ConnectionError("down"))
        fake_redis.pipeline = MagicMock(return_value=pipe)
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish("rider_1", {"type": "x"}, durable=True)

        assert result is True
        fake_redis.publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_non_durable_skips_seq_and_outbox_bookkeeping(self):
        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock()
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish("rider_1", {"type": "x"}, durable=False)

        assert result is True
        fake_redis.incr.assert_not_awaited()
        fake_redis.pipeline.assert_not_called()
        fake_redis.publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_non_serialisable_payload_returns_false(self):
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish("rider_1", {"bad": object()}, durable=False)

        assert result is False
        fake_redis.publish.assert_not_awaited()

    @pytest.mark.anyio
    async def test_bare_publish_error_returns_false(self):
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(side_effect=ConnectionError("down"))
        inst = _active_instance(fake_redis)

        result = await inst.publish("rider_1", {"type": "x"}, durable=False)

        assert result is False


# ── get_outbox() ─────────────────────────────────────────────────────────


class TestGetOutbox:
    @pytest.mark.anyio
    async def test_inactive_returns_empty_list(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        assert await inst.get_outbox("rider_1") == []

    @pytest.mark.anyio
    async def test_active_returns_parsed_messages(self):
        import json

        fake_redis = MagicMock()
        fake_redis.lrange = AsyncMock(return_value=[json.dumps({"seq": 1}), json.dumps({"seq": 2})])
        inst = _active_instance(fake_redis)

        result = await inst.get_outbox("rider_1")

        assert result == [{"seq": 1}, {"seq": 2}]
        fake_redis.lrange.assert_awaited_once_with("spinr:ws:outbox:rider_1", 0, -1)

    @pytest.mark.anyio
    async def test_read_failure_fails_open_to_empty_list(self):
        fake_redis = MagicMock()
        fake_redis.lrange = AsyncMock(side_effect=ConnectionError("down"))
        inst = _active_instance(fake_redis)

        result = await inst.get_outbox("rider_1")

        assert result == []


# ── publish_broadcast() ──────────────────────────────────────────────────


class TestPublishBroadcast:
    @pytest.mark.anyio
    async def test_inactive_returns_false(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        assert await inst.publish_broadcast("admin_", {"x": 1}) is False

    @pytest.mark.anyio
    async def test_success(self):
        import json

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish_broadcast("admin_", {"x": 1})

        assert result is True
        fake_redis.publish.assert_awaited_once()
        args = fake_redis.publish.await_args.args
        body = json.loads(args[1])
        assert body == {"kind": "broadcast", "prefix": "admin_", "message": {"x": 1}}

    @pytest.mark.anyio
    async def test_serialise_failure_returns_false(self):
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish_broadcast("admin_", {"bad": object()})

        assert result is False
        fake_redis.publish.assert_not_awaited()

    @pytest.mark.anyio
    async def test_publish_failure_returns_false(self):
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(side_effect=ConnectionError("down"))
        inst = _active_instance(fake_redis)

        result = await inst.publish_broadcast("admin_", {"x": 1})

        assert result is False


# ── publish_kick_user() ──────────────────────────────────────────────────


class TestPublishKickUser:
    @pytest.mark.anyio
    async def test_inactive_returns_false(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        assert await inst.publish_kick_user("user-1") is False

    @pytest.mark.anyio
    async def test_success(self):
        import json

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        result = await inst.publish_kick_user("user-1", client_types=["rider"], reason="token_revoked")

        assert result is True
        body = json.loads(fake_redis.publish.await_args.args[1])
        assert body == {
            "control": {
                "action": "kick_user",
                "user_id": "user-1",
                "client_types": ["rider"],
                "reason": "token_revoked",
            }
        }

    @pytest.mark.anyio
    async def test_publish_failure_returns_false(self):
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(side_effect=ConnectionError("down"))
        inst = _active_instance(fake_redis)

        result = await inst.publish_kick_user("user-1")

        assert result is False

    @pytest.mark.anyio
    async def test_serialise_failure_returns_false(self):
        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()
        inst = _active_instance(fake_redis)

        # A non-JSON-serialisable user_id makes json.dumps raise inside the
        # control envelope construction (avoids globally monkeypatching
        # json.dumps, which would also affect other code paths).
        result = await inst.publish_kick_user(object())

        assert result is False
        fake_redis.publish.assert_not_awaited()


# ── _reconnect() ─────────────────────────────────────────────────────────


class TestReconnect:
    @pytest.mark.anyio
    async def test_success_resubscribes(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        old_pubsub = MagicMock()
        old_pubsub.unsubscribe = AsyncMock()
        old_pubsub.close = AsyncMock()
        inst._pubsub = old_pubsub

        new_pubsub = MagicMock()
        new_pubsub.subscribe = AsyncMock(return_value=None)
        fake_redis = MagicMock()
        fake_redis.pubsub = MagicMock(return_value=new_pubsub)
        inst._redis = fake_redis

        result = await inst._reconnect()

        assert result is True
        assert inst._pubsub is new_pubsub
        old_pubsub.unsubscribe.assert_awaited_once()
        old_pubsub.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_failure_leaves_pubsub_none(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        inst._pubsub = None

        fake_redis = MagicMock()
        broken_pubsub = MagicMock()
        broken_pubsub.subscribe = AsyncMock(side_effect=RuntimeError("still down"))
        broken_pubsub.unsubscribe = AsyncMock()
        broken_pubsub.close = AsyncMock()
        fake_redis.pubsub = MagicMock(return_value=broken_pubsub)
        inst._redis = fake_redis

        result = await inst._reconnect()

        assert result is False
        assert inst._pubsub is None


# ── stop() / _safe_close_* ───────────────────────────────────────────────


class TestStopAndClose:
    @pytest.mark.anyio
    async def test_stop_cancels_task_and_closes_everything(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        inst._task = asyncio.create_task(asyncio.sleep(1000))
        pubsub_mock = MagicMock()
        pubsub_mock.unsubscribe = AsyncMock()
        pubsub_mock.close = AsyncMock()
        inst._pubsub = pubsub_mock
        redis_mock = MagicMock()
        redis_mock.close = AsyncMock()
        inst._redis = redis_mock
        inst._manager = MagicMock()

        await inst.stop()

        assert inst._task is None
        assert inst._pubsub is None
        assert inst._redis is None
        assert inst._manager is None
        pubsub_mock.unsubscribe.assert_awaited_once_with(ws_pubsub.CHANNEL)
        pubsub_mock.close.assert_awaited_once()
        redis_mock.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_stop_with_nothing_started_is_noop(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        await inst.stop()  # must not raise
        assert inst._task is None
        assert inst._pubsub is None
        assert inst._redis is None

    @pytest.mark.anyio
    async def test_safe_close_pubsub_swallows_unsubscribe_and_close_errors(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        pubsub_mock = MagicMock()
        pubsub_mock.unsubscribe = AsyncMock(side_effect=RuntimeError("boom"))
        pubsub_mock.close = AsyncMock(side_effect=RuntimeError("boom"))
        inst._pubsub = pubsub_mock

        await inst._safe_close_pubsub()  # must not raise

        assert inst._pubsub is None

    @pytest.mark.anyio
    async def test_safe_close_pubsub_noop_when_already_none(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        await inst._safe_close_pubsub()
        assert inst._pubsub is None

    @pytest.mark.anyio
    async def test_safe_close_redis_swallows_close_error(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        redis_mock = MagicMock()
        redis_mock.close = AsyncMock(side_effect=RuntimeError("boom"))
        inst._redis = redis_mock

        await inst._safe_close_redis()  # must not raise

        assert inst._redis is None

    @pytest.mark.anyio
    async def test_safe_close_redis_noop_when_already_none(self):
        from backend.utils import ws_pubsub

        inst = ws_pubsub._WSPubSub()
        await inst._safe_close_redis()
        assert inst._redis is None


# ── _consumer() ──────────────────────────────────────────────────────────


class TestConsumer:
    @pytest.mark.anyio
    async def test_cancelled_during_get_message_propagates(self):
        inst = _consumer_instance()
        inst._pubsub.get_message = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

    @pytest.mark.anyio
    async def test_falsy_message_is_skipped(self):
        inst = _consumer_instance()
        inst._pubsub.get_message = AsyncMock(side_effect=[None, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_not_awaited()

    @pytest.mark.anyio
    async def test_non_message_type_is_skipped(self):
        inst = _consumer_instance()
        inst._pubsub.get_message = AsyncMock(
            side_effect=[{"type": "subscribe"}, asyncio.CancelledError()]
        )

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_not_awaited()

    @pytest.mark.anyio
    async def test_invalid_json_is_skipped(self):
        inst = _consumer_instance()
        inst._pubsub.get_message = AsyncMock(
            side_effect=[{"type": "message", "data": "not-json"}, asyncio.CancelledError()]
        )

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_not_awaited()

    @pytest.mark.anyio
    async def test_control_kick_user_dispatches_to_manager(self):
        inst = _consumer_instance()
        frame = _frame(
            {
                "control": {
                    "action": "kick_user",
                    "user_id": "user-1",
                    "client_types": ["rider"],
                    "reason": "token_revoked",
                }
            }
        )
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager.disconnect_user.assert_awaited_once_with(
            "user-1", client_types=["rider"], reason="token_revoked"
        )

    @pytest.mark.anyio
    async def test_control_kick_user_defaults_when_fields_missing(self):
        inst = _consumer_instance()
        frame = _frame({"control": {"action": "kick_user"}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager.disconnect_user.assert_awaited_once_with(
            "", client_types=None, reason="token_revoked"
        )

    @pytest.mark.anyio
    async def test_control_unknown_action_is_ignored(self):
        inst = _consumer_instance()
        frame = _frame({"control": {"action": "reload_app"}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager.disconnect_user.assert_not_awaited()

    @pytest.mark.anyio
    async def test_control_dispatch_error_is_swallowed_and_loop_continues(self):
        inst = _consumer_instance()
        inst._manager.disconnect_user = AsyncMock(side_effect=RuntimeError("boom"))
        frame = _frame({"control": {"action": "kick_user", "user_id": "user-1"}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()  # must not propagate the RuntimeError

    @pytest.mark.anyio
    async def test_payload_none_is_skipped(self):
        inst = _consumer_instance()
        frame = _frame({"kind": "unicast", "client_id": "rider_1", "message": None})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_not_awaited()

    @pytest.mark.anyio
    async def test_broadcast_empty_prefix_is_skipped(self):
        inst = _consumer_instance()
        frame = _frame({"kind": "broadcast", "prefix": "", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_broadcast_local.assert_not_awaited()

    @pytest.mark.anyio
    async def test_broadcast_delivers_locally(self):
        inst = _consumer_instance()
        frame = _frame({"kind": "broadcast", "prefix": "admin_", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_broadcast_local.assert_awaited_once_with("admin_", {"x": 1})

    @pytest.mark.anyio
    async def test_broadcast_delivery_error_is_swallowed(self):
        inst = _consumer_instance()
        inst._manager._deliver_broadcast_local = AsyncMock(side_effect=RuntimeError("boom"))
        frame = _frame({"kind": "broadcast", "prefix": "admin_", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()  # must not propagate

    @pytest.mark.anyio
    async def test_unicast_missing_client_id_is_skipped(self):
        inst = _consumer_instance()
        frame = _frame({"kind": "unicast", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_not_awaited()

    @pytest.mark.anyio
    async def test_unicast_delivers_locally(self):
        inst = _consumer_instance()
        frame = _frame({"kind": "unicast", "client_id": "rider_1", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_awaited_once_with({"x": 1}, "rider_1")

    @pytest.mark.anyio
    async def test_missing_kind_defaults_to_unicast(self):
        """Backwards compat: older envelopes had no "kind" field and were
        always unicast, so a rolling deploy doesn't drop in-flight messages."""
        inst = _consumer_instance()
        frame = _frame({"client_id": "rider_1", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()

        inst._manager._deliver_local.assert_awaited_once_with({"x": 1}, "rider_1")

    @pytest.mark.anyio
    async def test_unicast_delivery_error_is_swallowed(self):
        inst = _consumer_instance()
        inst._manager._deliver_local = AsyncMock(side_effect=RuntimeError("boom"))
        frame = _frame({"kind": "unicast", "client_id": "rider_1", "message": {"x": 1}})
        inst._pubsub.get_message = AsyncMock(side_effect=[frame, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await inst._consumer()  # must not propagate

    @pytest.mark.anyio
    async def test_read_error_below_threshold_sleeps_and_continues(self):
        from backend.utils import ws_pubsub

        inst = _consumer_instance()
        inst._pubsub.get_message = AsyncMock(side_effect=[RuntimeError("boom"), asyncio.CancelledError()])
        sleep_mock = AsyncMock()

        with patch.object(ws_pubsub.asyncio, "sleep", sleep_mock):
            with pytest.raises(asyncio.CancelledError):
                await inst._consumer()

        sleep_mock.assert_awaited_once_with(1.0)

    @pytest.mark.anyio
    async def test_reconnect_success_at_threshold_resets_error_count(self):
        from backend.utils import ws_pubsub

        inst = _consumer_instance()
        errors = [RuntimeError("boom")] * 5
        inst._pubsub.get_message = AsyncMock(side_effect=[*errors, asyncio.CancelledError()])
        inst._reconnect = AsyncMock(return_value=True)
        sleep_mock = AsyncMock()

        with patch.object(ws_pubsub.asyncio, "sleep", sleep_mock):
            with pytest.raises(asyncio.CancelledError):
                await inst._consumer()

        inst._reconnect.assert_awaited_once()
        # errors 1-4 each sleep 1.0s; the 5th (threshold) reconnects instead
        # of sleeping, so exactly 4 backoff sleeps are expected.
        assert sleep_mock.await_count == 4
        for call in sleep_mock.await_args_list:
            assert call.args == (1.0,)

    @pytest.mark.anyio
    async def test_reconnect_failure_backs_off_five_seconds(self):
        from backend.utils import ws_pubsub

        inst = _consumer_instance()
        errors = [RuntimeError("boom")] * 5
        inst._pubsub.get_message = AsyncMock(side_effect=[*errors, asyncio.CancelledError()])
        inst._reconnect = AsyncMock(return_value=False)
        sleep_mock = AsyncMock()

        with patch.object(ws_pubsub.asyncio, "sleep", sleep_mock):
            with pytest.raises(asyncio.CancelledError):
                await inst._consumer()

        inst._reconnect.assert_awaited_once()
        assert sleep_mock.await_count == 5  # 4x 1.0s backoff + 1x 5.0s post-reconnect-failure backoff
        assert sleep_mock.await_args_list[-1].args == (5.0,)


# ── resolve_ws_redis_url() ───────────────────────────────────────────────


class TestResolveWsRedisUrl:
    def test_prefers_ws_url_when_set(self):
        from backend.utils.ws_pubsub import resolve_ws_redis_url

        assert resolve_ws_redis_url("redis://ws-host:6379/0", "redis://rl-host:6379/0") == "redis://ws-host:6379/0"

    def test_falls_back_to_rate_limit_url(self):
        from backend.utils.ws_pubsub import resolve_ws_redis_url

        assert resolve_ws_redis_url("", "redis://rl-host:6379/0") == "redis://rl-host:6379/0"

    def test_both_empty_returns_empty_string(self):
        from backend.utils.ws_pubsub import resolve_ws_redis_url

        assert resolve_ws_redis_url("", "") == ""

    def test_whitespace_only_ws_url_falls_back(self):
        from backend.utils.ws_pubsub import resolve_ws_redis_url

        assert resolve_ws_redis_url("   ", "redis://rl-host:6379/0") == "redis://rl-host:6379/0"

    def test_values_are_stripped(self):
        from backend.utils.ws_pubsub import resolve_ws_redis_url

        assert resolve_ws_redis_url("  redis://ws-host:6379/0  ", "") == "redis://ws-host:6379/0"
