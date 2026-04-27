"""B-P1-12 — pin the per-USER WebSocket message rate-limit.

Before this work the receive loop in routes/websocket.py enforced a
30 msg/s cap with a closure-scoped timestamp list. A user who opened
N sockets got N×30 effective throughput because each socket had its
own list. This was the side-step the runbook (docs/runbooks/websockets.md)
calls out.

These tests pin the new contract:

  1. note_user_message aggregates timestamps across every socket
     the user has open on this machine (the WS receive loop calls
     it with the authenticated user's id, not the connection_id).
  2. Two distinct user_ids each get their own budget — one user's
     burst doesn't starve another.
  3. Old timestamps roll out of the 1-second window so steady-state
     traffic at the cap is sustainable.
  4. The rate-limit bucket is dropped when the user's last socket
     disconnects (memory bound under churn).
  5. disconnect_user (the B-P1-11 force-kick path) also clears
     the bucket — kicked users start with a clean slate if they
     immediately re-auth.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# note_user_message — the core aggregation primitive
# ─────────────────────────────────────────────────────────────────────────────


class TestNoteUserMessage:
    """Pin manager.note_user_message — the per-user bucket update.

    Synchronous on purpose: we don't need an async harness here since
    the method uses time.monotonic() rather than the asyncio loop's
    time(). That keeps the tests fast and the production code one
    layer simpler (no spurious DeprecationWarning from
    asyncio.get_event_loop() in a sync context)."""

    def test_allows_messages_under_cap(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        # 30 msg/s default. 29 should all return True.
        results = [mgr.note_user_message("user-a") for _ in range(29)]
        assert all(results), f"expected 29 Trues, got {results}"

    def test_rejects_message_exceeding_cap(self):
        """30/s cap, 31st in same window must return False."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(30):
            assert mgr.note_user_message("user-a") is True
        assert mgr.note_user_message("user-a") is False
        # Repeated reject calls don't blow the bucket: still False.
        assert mgr.note_user_message("user-a") is False

    def test_aggregates_across_simulated_multi_socket_user(self):
        """The whole point of B-P1-12. A driver app opens its socket
        AND the rider app opens its socket on the same authenticated
        user (rare but possible — admin staff who also uses the rider
        app on the same phone). The cap is per-USER, not per-socket,
        so 31 messages from any combination of those sockets must
        still trip the limit on the 31st."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        # Interleave the calls as if two sockets are racing through
        # the receive loop. The user_id passed is the same — the
        # client_type prefix is irrelevant here because note_user_message
        # is keyed only on user_id.
        seen_false = False
        for i in range(40):
            ok = mgr.note_user_message("dual-socket-user")
            if not ok:
                seen_false = True
                # The first reject must arrive on or after the 31st
                # call (1-based) since we allowed 30 before.
                assert i == 30, f"first reject at {i}, expected 30"
                break
        assert seen_false, "never tripped the cap"

    def test_other_users_unaffected_by_one_users_burst(self):
        """A noisy user must not starve every other user. Each user
        has an independent bucket."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(30):
            mgr.note_user_message("noisy")
        assert mgr.note_user_message("noisy") is False
        # Quiet user gets full budget.
        for _ in range(30):
            assert mgr.note_user_message("quiet") is True

    def test_window_slides_over_one_second(self):
        """After the 1-second window passes, the bucket trims and the
        user can send again. We sleep 1.05s to ensure all timestamps
        fall outside the window — a rounded 1.0s could race the
        ``cutoff = now_ts - 1.0`` check on slow CI."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(30):
            mgr.note_user_message("user-c")
        assert mgr.note_user_message("user-c") is False

        time.sleep(1.05)
        # Old entries roll out → fresh budget.
        assert mgr.note_user_message("user-c") is True

    def test_custom_max_per_second_override(self):
        """The cap is configurable per-call so location-update-heavy
        endpoints can opt into a higher (or lower) limit if we ever
        decide to. For B-P1-12 the receive loop passes
        WS_MAX_MESSAGES_PER_SECOND, but the override exists so the
        method is reusable for future per-message-type caps."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        for _ in range(5):
            assert mgr.note_user_message("user-d", max_per_second=5) is True
        assert mgr.note_user_message("user-d", max_per_second=5) is False


# ─────────────────────────────────────────────────────────────────────────────
# Bucket lifecycle — cleanup on disconnect / disconnect_user
# ─────────────────────────────────────────────────────────────────────────────


class TestBucketCleanup:
    """Pin the memory-bound contract: when a user has no more sockets
    on this machine, their per-user bucket is evicted. Without this
    the dict would grow forever as users come and go."""

    @pytest.mark.asyncio
    async def test_disconnect_drops_bucket_when_last_socket_closes(self):
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_user-e")
        mgr.note_user_message("user-e")
        assert "user-e" in mgr._user_msg_timestamps

        mgr.disconnect("rider_user-e")
        assert "user-e" not in mgr._user_msg_timestamps

    @pytest.mark.asyncio
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
        mgr.note_user_message("user-f")

        mgr.disconnect("rider_user-f")
        assert "user-f" in mgr._user_msg_timestamps, (
            "bucket dropped while driver socket still open — would let "
            "user reset their budget by toggling the rider socket"
        )

        mgr.disconnect("driver_user-f")
        assert "user-f" not in mgr._user_msg_timestamps

    @pytest.mark.asyncio
    async def test_disconnect_user_clears_bucket(self):
        """The B-P1-11 kick path closes every socket for a user. The
        bucket should also clear so the user starts with a fresh budget
        if they immediately re-auth (the bucket entries from before the
        kick are stale signal — we just severed those sockets)."""
        from backend.socket_manager import ConnectionManager

        mgr = ConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, "rider_user-g")
        mgr.note_user_message("user-g")
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
