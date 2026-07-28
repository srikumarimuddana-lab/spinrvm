"""B-P1-12 / B4 — pin the per-USER WebSocket message rate-limit.

Before B-P1-12 the receive loop in routes/websocket.py enforced a
30 msg/s cap with a closure-scoped timestamp list. A user who opened
N sockets got N×30 effective throughput because each socket had its
own list. B-P1-12 fixed that by aggregating per-user on one machine —
but a user force-balanced across replicas still got up to
``replica_count × cap`` throughput, since each replica's
``ConnectionManager`` held its own in-process bucket. B4 promotes the
counter to Redis (fixed window: INCR + EXPIRE 1s), shared fleet-wide,
with an in-process sliding-window fallback if Redis itself fails.

These tests pin the contract:

  1. note_user_message aggregates across every socket the user has
     open (the WS receive loop calls it with the authenticated user's
     id, not the connection_id) — via the shared Redis-backed counter.
  2. Two distinct user_ids each get their own budget — one user's
     burst doesn't starve another.
  3. The window resets after 1 second so steady-state traffic at the
     cap is sustainable.
  4. If the Redis call itself raises, the limiter fails OPEN to the
     original per-machine sliding-window bucket rather than blocking
     every WS message fleet-wide on a transient Redis hiccup — and
     that fallback bucket is memory-bounded the same way B-P1-12
     originally shipped: dropped when the user's last socket
     disconnects, and by disconnect_user (the B-P1-11 force-kick path).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

# ─────────────────────────────────────────────────────────────────────────────
# note_user_message — the Redis-backed fleet-wide path
# ─────────────────────────────────────────────────────────────────────────────


class TestNoteUserMessageRedisBacked:
    """Pin manager.note_user_message's primary path. ``mock_redis`` gives
    each test a fresh in-process store standing in for Redis (via
    utils/redis_client.py's transparent fallback), so these exercise the
    real fixed-window INCR+EXPIRE logic without a real Redis server."""

    async def test_allows_messages_under_cap(self, mock_redis):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        # 30 msg/s default. 29 should all return True.
        results = [await mgr.note_user_message("user-a") for _ in range(29)]
        assert all(results), f"expected 29 Trues, got {results}"

    async def test_rejects_message_exceeding_cap(self, mock_redis):
        """30/s cap, 31st in the same window must return False."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(30):
            assert await mgr.note_user_message("user-a") is True
        assert await mgr.note_user_message("user-a") is False
        # Repeated reject calls don't blow the counter: still False.
        assert await mgr.note_user_message("user-a") is False

    async def test_aggregates_across_simulated_multi_socket_user(self, mock_redis):
        """The whole point of B-P1-12/B4. A driver app opens its socket
        AND the rider app opens its socket on the same authenticated
        user (rare but possible — admin staff who also uses the rider
        app on the same phone). The cap is per-USER, not per-socket,
        so 31 messages from any combination of those sockets must
        still trip the limit on the 31st — and since the counter is
        Redis-backed, that holds even if the sockets were on different
        replicas."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        seen_false = False
        for i in range(40):
            ok = await mgr.note_user_message("dual-socket-user")
            if not ok:
                seen_false = True
                # The first reject must arrive on or after the 31st
                # call (1-based) since we allowed 30 before.
                assert i == 30, f"first reject at {i}, expected 30"
                break
        assert seen_false, "never tripped the cap"

    async def test_other_users_unaffected_by_one_users_burst(self, mock_redis):
        """A noisy user must not starve every other user. Each user
        has an independent counter."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(30):
            await mgr.note_user_message("noisy")
        assert await mgr.note_user_message("noisy") is False
        # Quiet user gets full budget.
        for _ in range(30):
            assert await mgr.note_user_message("quiet") is True

    async def test_window_resets_after_one_second(self, mock_redis):
        """After the fixed 1-second window expires (the EXPIRE set on
        the counter's first increment), the next message starts a fresh
        window. We sleep 1.05s to give the TTL a safety margin on slow
        CI."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(30):
            await mgr.note_user_message("user-c")
        assert await mgr.note_user_message("user-c") is False

        time.sleep(1.05)
        # The Redis key's TTL has expired → fresh budget.
        assert await mgr.note_user_message("user-c") is True

    async def test_custom_max_per_second_override(self, mock_redis):
        """The cap is configurable per-call so location-update-heavy
        endpoints can opt into a higher (or lower) limit if we ever
        decide to. For B-P1-12/B4 the receive loop passes
        WS_MAX_MESSAGES_PER_SECOND, but the override exists so the
        method is reusable for future per-message-type caps."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(5):
            assert await mgr.note_user_message("user-d", max_per_second=5) is True
        assert await mgr.note_user_message("user-d", max_per_second=5) is False


# ─────────────────────────────────────────────────────────────────────────────
# note_user_message — fail-open fallback when Redis itself errors
# ─────────────────────────────────────────────────────────────────────────────


class TestNoteUserMessageFallback:
    """When Redis is configured but a call raises (network blip, Redis
    down), note_user_message must fail OPEN to the original per-machine
    sliding-window bucket rather than blocking every WS message
    fleet-wide. Forced here by patching redis_incr to raise."""

    async def test_falls_back_to_local_bucket_on_redis_error(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        with patch("backend.socket_manager.redis_incr", AsyncMock(side_effect=ConnectionError("redis down"))):
            for _ in range(30):
                assert await mgr.note_user_message("user-h") is True
            assert await mgr.note_user_message("user-h") is False
        # The fallback path populated the local sliding-window bucket.
        assert "user-h" in mgr._user_msg_timestamps

    async def test_local_fallback_window_slides(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        with patch("backend.socket_manager.redis_incr", AsyncMock(side_effect=ConnectionError("redis down"))):
            for _ in range(30):
                await mgr.note_user_message("user-i")
            assert await mgr.note_user_message("user-i") is False
            time.sleep(1.05)
            assert await mgr.note_user_message("user-i") is True


# ─────────────────────────────────────────────────────────────────────────────
# Bucket lifecycle — cleanup on disconnect / disconnect_user (fallback only)
# ─────────────────────────────────────────────────────────────────────────────


class TestBucketCleanup:
    """Pin the memory-bound contract of the per-machine FALLBACK bucket:
    when a user has no more sockets on this machine, their bucket is
    evicted. Without this the dict would grow forever as users come and
    go across Redis outages. (The primary Redis-backed path needs no
    such cleanup — its keys expire via TTL on their own.) Forced onto
    the fallback path here by patching redis_incr to raise."""

    def _redis_down(self):
        return patch("backend.socket_manager.redis_incr", AsyncMock(side_effect=ConnectionError("redis down")))

    async def test_disconnect_drops_bucket_when_last_socket_closes(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_user-e")
        with self._redis_down():
            await mgr.note_user_message("user-e")
        assert "user-e" in mgr._user_msg_timestamps

        mgr.disconnect("rider_user-e")
        assert "user-e" not in mgr._user_msg_timestamps

    async def test_disconnect_keeps_bucket_when_other_socket_remains(self):
        """User has rider + driver sockets. Closing one must NOT drop
        the bucket — the other socket still uses it. Otherwise a user
        with two clients would silently get a fresh budget every time
        either client reconnected."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws_rider = AsyncMock()
        ws_driver = AsyncMock()
        await mgr.connect(ws_rider, "rider_user-f")
        await mgr.connect(ws_driver, "driver_user-f")
        with self._redis_down():
            await mgr.note_user_message("user-f")

        mgr.disconnect("rider_user-f")
        assert "user-f" in mgr._user_msg_timestamps, (
            "bucket dropped while driver socket still open — would let "
            "user reset their budget by toggling the rider socket"
        )

        mgr.disconnect("driver_user-f")
        assert "user-f" not in mgr._user_msg_timestamps

    async def test_disconnect_user_clears_bucket(self):
        """The B-P1-11 kick path closes every socket for a user. The
        bucket should also clear so the user starts with a fresh budget
        if they immediately re-auth (the bucket entries from before the
        kick are stale signal — we just severed those sockets)."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_user-g")
        with self._redis_down():
            await mgr.note_user_message("user-g")
        assert "user-g" in mgr._user_msg_timestamps

        await mgr.disconnect_user("user-g", client_types=["rider"])
        assert "user-g" not in mgr._user_msg_timestamps

    def test_disconnect_with_unknown_key_shape_does_not_raise(self):
        """Defensive: if active_connections somehow contained a key not
        matching {client_type}_{user_id} (it shouldn't, but a future
        refactor could add one), disconnect must not crash. The
        per-user cleanup short-circuits via _user_id_from_key returning
        None — we just skip the bucket pop."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        # No connect — disconnect a key that was never registered AND
        # doesn't match the expected prefix shape.
        mgr.disconnect("unknown_format_key")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Helper coverage — _user_id_from_key
# ─────────────────────────────────────────────────────────────────────────────


class TestUserIdFromKey:
    """Pin the connection-key parser. If this drifts (e.g. a new
    client_type prefix is added in routes/websocket.py without
    updating the parser), the per-user bucket cleanup would silently
    leak memory."""

    def test_parses_each_client_type_prefix(self):
        from backend.socket_manager import ConnectionManager

        assert ConnectionManager._user_id_from_key("rider_abc") == "abc"
        assert ConnectionManager._user_id_from_key("driver_xyz123") == "xyz123"
        assert ConnectionManager._user_id_from_key("admin_staff-001") == "staff-001"

    def test_returns_none_for_unknown_prefix(self):
        from backend.socket_manager import ConnectionManager

        assert ConnectionManager._user_id_from_key("corporate_admin-1") is None
        assert ConnectionManager._user_id_from_key("just-a-string") is None
        assert ConnectionManager._user_id_from_key("") is None
