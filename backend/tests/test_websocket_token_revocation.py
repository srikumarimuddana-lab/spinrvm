"""B-P1-11 — pin the WebSocket-side contract for token_version revocation.

Without this work, /auth/logout-all (B-P1-13) and the B-P1-3 reuse
cascade left a hole: long-lived WebSocket sockets only verify the JWT
once at connect time, so a user who hit "Sign out everywhere" still
had a live WS pushing dispatch/ride events until the socket happened
to drop on its own. These tests pin the three closing mechanisms:

  1. ConnectionManager.disconnect_user — closes local sockets for a
     user, scoped by client_type. The atomic primitive every other
     piece builds on.
  2. ConnectionManager.kick_user — wraps disconnect_user with a Redis
     pub/sub fan-out so multi-replica deployments kick the user on
     every VM, not just the caller's.
  3. ws_pubsub.publish_kick_user + consumer dispatch — the cross-VM
     wire format (control envelope) and the consumer's routing of
     control messages back into manager.disconnect_user.
  4. heartbeat_task token_version re-check — the safety-net that
     catches sockets even when the kick fan-out failed.

If any of these break, the runbook recovery path
(docs/runbooks/auth-tokens.md → "Forcing a manual session kill")
silently degrades.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# ConnectionManager.disconnect_user
# ─────────────────────────────────────────────────────────────────────────────


class TestDisconnectUser:
    """Pin disconnect_user behaviour. Lives in backend/socket_manager.py."""

    @pytest.mark.asyncio
    async def test_kicks_only_matching_user_and_scoped_client_types(self):
        """Kicks rider_u1 + driver_u1 but not admin_u1 (not in scope)
        and not rider_u2 (different user)."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        rider_u1 = AsyncMock()
        driver_u1 = AsyncMock()
        admin_u1 = AsyncMock()
        rider_u2 = AsyncMock()
        await mgr.connect(rider_u1, "rider_u1")
        await mgr.connect(driver_u1, "driver_u1")
        await mgr.connect(admin_u1, "admin_u1")
        await mgr.connect(rider_u2, "rider_u2")

        closed = await mgr.disconnect_user(
            "u1",
            client_types=["rider", "driver"],
            reason="logout_all",
        )

        assert closed == 2
        assert "rider_u1" not in mgr.active_connections
        assert "driver_u1" not in mgr.active_connections
        # Other-user + out-of-scope sockets untouched.
        assert "admin_u1" in mgr.active_connections
        assert "rider_u2" in mgr.active_connections
        # Each kicked socket got the reason frame before close.
        rider_u1.send_json.assert_awaited_once_with({"type": "session_revoked", "reason": "logout_all"})
        driver_u1.send_json.assert_awaited_once_with({"type": "session_revoked", "reason": "logout_all"})
        rider_u1.close.assert_awaited_once()
        driver_u1.close.assert_awaited_once()
        # Untouched sockets must NOT have been closed.
        admin_u1.close.assert_not_awaited()
        rider_u2.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idempotent_on_missing_socket(self):
        """Kicking a user with no live sockets returns 0 without raising —
        callers (logout_all) treat this as no-op and proceed."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        closed = await mgr.disconnect_user("ghost_user", client_types=["rider"])
        assert closed == 0

    @pytest.mark.asyncio
    async def test_default_client_types_covers_rider_driver_admin(self):
        """When caller passes client_types=None, all three surfaces are kicked."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for ct in ("rider", "driver", "admin"):
            await mgr.connect(AsyncMock(), f"{ct}_u3")

        closed = await mgr.disconnect_user("u3")

        assert closed == 3
        assert mgr.active_connections == {}

    @pytest.mark.asyncio
    async def test_close_failure_still_removes_from_registry(self):
        """If ws.close raises (socket already torn down server-side), the
        registry entry is still removed — leaving it would let
        send_personal_message try to write to a dead handle."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
        await mgr.connect(ws, "rider_u4")

        closed = await mgr.disconnect_user("u4", client_types=["rider"])

        assert closed == 1
        assert "rider_u4" not in mgr.active_connections


# ─────────────────────────────────────────────────────────────────────────────
# ConnectionManager.kick_user (cross-replica wrapper)
# ─────────────────────────────────────────────────────────────────────────────


class TestKickUser:
    """Pin kick_user — the multi-replica entry point. Always disconnects
    LOCAL sockets and best-effort publishes to the Redis channel so other
    VMs disconnect their own copies.

    Patching note: ``socket_manager.kick_user`` uses the bare-import form
    ``from utils.ws_pubsub import pubsub`` (matching the codebase's dual-
    import convention). Because conftest puts both ``backend/`` and the
    project root on sys.path, a top-level patch on
    ``backend.utils.ws_pubsub.pubsub.<x>`` would target a DIFFERENT
    module/singleton than the one ``kick_user`` actually reaches at run
    time. We import the singleton via the same path the code uses and
    patch the method directly on that instance — guarantees the patch
    lands."""

    @pytest.mark.asyncio
    async def test_publishes_then_disconnects_locally(self):
        from backend.socket_manager import ConnectionManager
        from utils.ws_pubsub import pubsub as code_pubsub

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_u5")

        with patch.object(
            code_pubsub,
            "publish_kick_user",
            AsyncMock(return_value=True),
        ) as publish_kick:
            closed = await mgr.kick_user(
                "u5",
                client_types=["rider"],
                reason="logout_all",
            )

        publish_kick.assert_awaited_once_with(
            "u5",
            client_types=["rider"],
            reason="logout_all",
        )
        assert closed == 1
        assert "rider_u5" not in mgr.active_connections

    @pytest.mark.asyncio
    async def test_local_kick_runs_even_when_publish_returns_false(self):
        """Single-machine mode (no Redis) — publish returns False; local
        kick MUST still happen, otherwise /auth/logout-all on dev/single-VM
        deployments silently leaks live sockets."""
        from backend.socket_manager import ConnectionManager
        from utils.ws_pubsub import pubsub as code_pubsub

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "driver_u6")

        with patch.object(
            code_pubsub,
            "publish_kick_user",
            AsyncMock(return_value=False),
        ):
            closed = await mgr.kick_user("u6", client_types=["driver"])

        assert closed == 1
        assert "driver_u6" not in mgr.active_connections

    @pytest.mark.asyncio
    async def test_publish_exception_does_not_block_local_kick(self):
        """A Redis hiccup at publish time must not stop the local close —
        the heartbeat is the safety net but it won't tick for 30s, and
        the operator pressing 'Sign out everywhere' expects synchronous
        local effect."""
        from backend.socket_manager import ConnectionManager
        from utils.ws_pubsub import pubsub as code_pubsub

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_u7")

        with patch.object(
            code_pubsub,
            "publish_kick_user",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            closed = await mgr.kick_user("u7", client_types=["rider"])

        assert closed == 1
        assert "rider_u7" not in mgr.active_connections


# ─────────────────────────────────────────────────────────────────────────────
# ws_pubsub control-message wire format + consumer dispatch
# ─────────────────────────────────────────────────────────────────────────────


class TestPubSubKickConsumer:
    """Pin the cross-replica wire shape. Two parts:
    1. publish_kick_user emits a control-shaped JSON envelope.
    2. The consumer routes that envelope to manager.disconnect_user
       rather than the legacy _deliver_local path."""

    @pytest.mark.asyncio
    async def test_publish_kick_user_emits_control_envelope(self):
        """The on-wire shape is {"control": {action, user_id, client_types,
        reason}}. Distinct from the legacy {"client_id", "message"} shape
        so the consumer can route on payload structure."""
        import json

        from backend.utils.ws_pubsub import _WSPubSub

        ps = _WSPubSub()
        ps._redis = MagicMock()
        ps._redis.publish = AsyncMock()
        # Force active=True without a real consumer task or subscription.
        ps._pubsub = MagicMock()
        ps._task = MagicMock()
        ps._task.done = MagicMock(return_value=False)

        ok = await ps.publish_kick_user(
            "u8",
            client_types=["rider", "driver"],
            reason="logout_all",
        )

        assert ok is True
        ps._redis.publish.assert_awaited_once()
        channel, body = ps._redis.publish.await_args.args
        assert channel == "spinr:ws:dispatch"
        decoded = json.loads(body)
        assert decoded == {
            "control": {
                "action": "kick_user",
                "user_id": "u8",
                "client_types": ["rider", "driver"],
                "reason": "logout_all",
            }
        }

    @pytest.mark.asyncio
    async def test_publish_kick_user_returns_false_when_inactive(self):
        """No Redis configured → publish_kick_user is a no-op returning
        False so the caller falls back to local-only kicks."""
        from backend.utils.ws_pubsub import _WSPubSub

        ps = _WSPubSub()
        # _redis is None / _task is None → active is False
        ok = await ps.publish_kick_user("u9")
        assert ok is False

    @pytest.mark.asyncio
    async def test_consumer_dispatches_kick_to_manager(self):
        """A control envelope arriving on the channel must call
        manager.disconnect_user with the right scope, NOT the legacy
        _deliver_local. Pins the consumer's routing branch."""
        import asyncio
        import json

        from backend.utils.ws_pubsub import _WSPubSub

        ps = _WSPubSub()
        # Stub the manager so we observe dispatch.
        manager_mock = MagicMock()
        manager_mock.disconnect_user = AsyncMock()
        manager_mock._deliver_local = AsyncMock()
        ps._manager = manager_mock

        # Build a fake pubsub object that yields one control message
        # then signals cancellation so the consumer exits cleanly.
        envelope = json.dumps(
            {
                "control": {
                    "action": "kick_user",
                    "user_id": "u10",
                    "client_types": ["admin"],
                    "reason": "refresh_token_reuse",
                }
            }
        )
        messages = iter(
            [
                {"type": "message", "data": envelope},
                None,  # idle tick
            ]
        )

        async def fake_get_message(ignore_subscribe_messages, timeout):
            try:
                return next(messages)
            except StopIteration:
                # Cancel once exhausted so the consumer loop exits.
                raise asyncio.CancelledError

        fake_pubsub = MagicMock()
        fake_pubsub.get_message = fake_get_message
        ps._pubsub = fake_pubsub

        with pytest.raises(asyncio.CancelledError):
            await ps._consumer()

        manager_mock.disconnect_user.assert_awaited_once_with(
            "u10",
            client_types=["admin"],
            reason="refresh_token_reuse",
        )
        # Legacy delivery path MUST NOT have fired for a control message.
        manager_mock._deliver_local.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# heartbeat_task token_version re-validation
# ─────────────────────────────────────────────────────────────────────────────


class TestHeartbeatTokenVersionRecheck:
    """Pin the safety-net path. Without the kick_user fan-out (Redis
    down, single-machine mode skipped, etc.) the heartbeat must still
    catch a stale-token socket within HEARTBEAT_INTERVAL seconds."""

    @pytest.mark.asyncio
    async def test_closes_socket_when_stored_version_exceeds_claim(self):
        """User claim_token_version=1, DB shows 2 → close with code 1008
        and break the heartbeat loop. We monkeypatch the sleep to fire
        instantly so the test runs in milliseconds."""
        import asyncio as _asyncio

        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        with (
            patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=2)),
            patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
        ):
            # Run the heartbeat — it should close on the first iteration.
            await _asyncio.wait_for(
                ws_mod.heartbeat_task(
                    ws,
                    "rider_u11",
                    user_id="u11",
                    claim_token_version=1,
                ),
                timeout=1.0,
            )

        # Reason frame sent BEFORE the close.
        ws.send_json.assert_any_await({"type": "session_revoked", "reason": "token_revoked"})
        ws.close.assert_awaited_once()
        # Must not have sent a ping after deciding to close.
        ping_calls = [
            c
            for c in ws.send_json.await_args_list
            if c.args and isinstance(c.args[0], dict) and c.args[0].get("type") == "ping"
        ]
        assert ping_calls == []

    @pytest.mark.asyncio
    async def test_does_not_close_when_versions_equal(self):
        """Stable session — DB matches claim. Heartbeat sends a ping and
        then we cancel to exit the loop. Nothing should have closed."""
        import asyncio as _asyncio

        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        ws.send_json = AsyncMock()
        # First sleep returns; subsequent send_json("ping") raises so the
        # loop breaks naturally without us cancelling.
        ping_seen = _asyncio.Event()

        async def maybe_break(payload):
            if isinstance(payload, dict) and payload.get("type") == "ping":
                ping_seen.set()
                raise RuntimeError("simulated drop after ping")

        ws.send_json.side_effect = maybe_break

        with (
            patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=3)),
            patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
        ):
            await _asyncio.wait_for(
                ws_mod.heartbeat_task(
                    ws,
                    "driver_u12",
                    user_id="u12",
                    claim_token_version=3,
                ),
                timeout=1.0,
            )

        assert ping_seen.is_set()
        # session_revoked frame must NOT have been sent.
        revoke_frames = [
            c
            for c in ws.send_json.await_args_list
            if c.args and isinstance(c.args[0], dict) and c.args[0].get("type") == "session_revoked"
        ]
        assert revoke_frames == []
        # close() must NOT have been called for revocation reasons.
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_read_failure_does_not_close_socket(self):
        """A transient DB hiccup (Supabase 503, network blip) must not
        kill a healthy socket — we'd rather let one stale-token poll
        slip through than mass-disconnect the fleet on a network
        twitch. _read_token_version returns None on failure."""
        import asyncio as _asyncio

        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        ping_seen = _asyncio.Event()

        async def maybe_break(payload):
            if isinstance(payload, dict) and payload.get("type") == "ping":
                ping_seen.set()
                raise RuntimeError("simulated drop after ping")

        ws.send_json.side_effect = maybe_break

        with (
            patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=None)),
            patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
        ):
            await _asyncio.wait_for(
                ws_mod.heartbeat_task(
                    ws,
                    "rider_u13",
                    user_id="u13",
                    claim_token_version=0,
                ),
                timeout=1.0,
            )

        assert ping_seen.is_set()
        ws.close.assert_not_awaited()


class TestHeartbeatFirebaseWatermarkRecheck:
    """Firebase ID tokens carry no token_version claim, so the heartbeat must
    re-check the sessions_invalid_before watermark (against the token's
    auth_time) instead of token_version — mirroring the HTTP path and the WS
    handshake. A Firebase connection is signalled by firebase_auth_time."""

    @pytest.mark.asyncio
    async def test_closes_firebase_socket_when_watermark_revokes(self):
        import asyncio as _asyncio

        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        with (
            patch.object(ws_mod, "_read_firebase_session_revoked", AsyncMock(return_value=True)),
            patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=99)) as tv_reader,
            patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
        ):
            await _asyncio.wait_for(
                ws_mod.heartbeat_task(ws, "rider_fb1", user_id="fb1", firebase_auth_time=1000),
                timeout=1.0,
            )

        ws.send_json.assert_any_await({"type": "session_revoked", "reason": "token_revoked"})
        ws.close.assert_awaited_once()
        # Firebase path must NOT fall back to the token_version reader.
        tv_reader.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_close_firebase_socket_when_watermark_ok(self):
        import asyncio as _asyncio

        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        ping_seen = _asyncio.Event()

        async def maybe_break(payload):
            if isinstance(payload, dict) and payload.get("type") == "ping":
                ping_seen.set()
                raise RuntimeError("simulated drop after ping")

        ws.send_json.side_effect = maybe_break

        with (
            patch.object(ws_mod, "_read_firebase_session_revoked", AsyncMock(return_value=False)),
            patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
        ):
            await _asyncio.wait_for(
                ws_mod.heartbeat_task(ws, "rider_fb2", user_id="fb2", firebase_auth_time=2000),
                timeout=1.0,
            )

        assert ping_seen.is_set()
        revoke_frames = [
            c
            for c in ws.send_json.await_args_list
            if c.args and isinstance(c.args[0], dict) and c.args[0].get("type") == "session_revoked"
        ]
        assert revoke_frames == []
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_read_firebase_session_revoked_compares_watermark(self):
        from backend.routes import websocket as ws_mod

        # auth_time 1000 predates a watermark of 2000s → revoked.
        revoked_row = {"id": "fb3", "sessions_invalid_before": "1970-01-01T00:33:20+00:00"}
        with patch.object(ws_mod.db_supabase, "get_user_by_id", AsyncMock(return_value=revoked_row)):
            assert await ws_mod._read_firebase_session_revoked("rider_fb3", "fb3", 1000) is True

        # No watermark → not revoked.
        with patch.object(ws_mod.db_supabase, "get_user_by_id", AsyncMock(return_value={"id": "fb3"})):
            assert await ws_mod._read_firebase_session_revoked("rider_fb3", "fb3", 1000) is False

        # DB read failure → do not act.
        with patch.object(ws_mod.db_supabase, "get_user_by_id", AsyncMock(side_effect=RuntimeError("503"))):
            assert await ws_mod._read_firebase_session_revoked("rider_fb3", "fb3", 1000) is False
