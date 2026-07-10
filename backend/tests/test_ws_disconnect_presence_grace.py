"""Presence survives an *involuntary* WS disconnect, but not a forced kill.

Background
----------
A driver appears in the rider app (`/drivers/nearby` map pins and
`/rides/estimate`) only while a live Redis *presence* key exists for them;
admin instead reads the durable `drivers.is_online` column. The presence key
carries a 30s TTL (utils/driver_presence.PRESENCE_TTL) precisely so a flaky
mobile connection — socket drops, client reconnects a couple of seconds later
— does NOT yank the driver out of rider results for the length of the blip.

The regressions this pins
-------------------------
1. A plain `WebSocketDisconnect` / send-failure (network drop) must NOT clear
   the presence key — let the TTL lapse so a reconnect rides through. This is
   the fix for the reported "car online in admin but not in the rider app"
   caused by a flapping socket wiping presence on every reconnect.

2. A *revocation* close (Sign out everywhere / token-version bump / Firebase
   session invalidation) is a deliberate server kill — it MUST clear presence
   immediately (inside `heartbeat_task`), or `/drivers/nearby` + dispatch could
   keep the revoked driver reachable for up to 30s and offer a ride to a socket
   that no longer exists.

3. BUT that revocation clear must respect ownership: if the driver already
   reconnected (a newer socket registered under the same connection_key and
   called mark_present), the stale heartbeat must NOT wipe the live socket's
   presence key.

4. The explicit Go Offline handler (routes/drivers.py) must still clear
   presence on the `is_online=False` branch.
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, patch

import pytest


class TestHeartbeatRevocationPresence:
    """heartbeat_task force-closes a driver socket on session revocation. That
    path clears presence — but only when this socket still owns the key."""

    @pytest.mark.anyio
    async def test_revocation_clears_driver_presence(self):
        """Stored token_version (2) > claim (1) → revoked → presence cleared
        for the driver id, session_revoked frame sent, socket closed."""
        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        key = "driver_u1"
        ws_mod.manager.active_connections[key] = ws  # this socket owns the key
        try:
            with (
                patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=2)),
                patch.object(ws_mod, "clear_presence", AsyncMock()) as clear_mock,
                patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
            ):
                await ws_mod.heartbeat_task(
                    ws, key, user_id="u1", driver_id="drv-1", claim_token_version=1
                )
        finally:
            ws_mod.manager.active_connections.pop(key, None)

        clear_mock.assert_awaited_once_with("drv-1")
        ws.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_stale_heartbeat_does_not_clear_presence_after_reconnect(self):
        """The driver reconnected: a NEWER socket owns connection_key and has
        already marked presence. This stale heartbeat detecting revocation of
        the OLD token must close its own socket but NOT clear the live socket's
        presence key."""
        from backend.routes import websocket as ws_mod

        old_ws = AsyncMock()
        new_ws = AsyncMock()
        key = "driver_u1"
        ws_mod.manager.active_connections[key] = new_ws  # newer socket owns it
        try:
            with (
                patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=2)),
                patch.object(ws_mod, "clear_presence", AsyncMock()) as clear_mock,
                patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
            ):
                await ws_mod.heartbeat_task(
                    old_ws, key, user_id="u1", driver_id="drv-1", claim_token_version=1
                )
        finally:
            ws_mod.manager.active_connections.pop(key, None)

        clear_mock.assert_not_awaited()  # live socket's presence preserved
        old_ws.close.assert_awaited_once()  # stale socket still closed

    @pytest.mark.anyio
    async def test_network_drop_does_not_clear_presence(self):
        """Not revoked (versions equal); the ping then fails to send (socket
        died). That send-failure path is a network drop — presence must be left
        for the TTL, NOT cleared."""
        from backend.routes import websocket as ws_mod

        ws = AsyncMock()
        ping_seen = {"v": False}

        async def maybe_break(payload):
            if isinstance(payload, dict) and payload.get("type") == "ping":
                ping_seen["v"] = True
                raise RuntimeError("simulated drop after ping")

        ws.send_json.side_effect = maybe_break

        with (
            patch.object(ws_mod, "_read_token_version", AsyncMock(return_value=3)),
            patch.object(ws_mod, "clear_presence", AsyncMock()) as clear_mock,
            patch.object(ws_mod.asyncio, "sleep", AsyncMock(return_value=None)),
        ):
            await ws_mod.heartbeat_task(
                ws, "driver_u2", user_id="u2", driver_id="drv-2", claim_token_version=3
            )

        assert ping_seen["v"]
        clear_mock.assert_not_awaited()


def test_involuntary_disconnect_branches_do_not_clear_presence():
    """The WebSocketDisconnect / generic-Exception branches of the endpoint must
    not clear presence — a plain network drop rides the 30s TTL. (Revocation is
    handled in heartbeat_task, which is a different code region.)"""
    from backend.routes import websocket as ws_mod

    src = inspect.getsource(ws_mod.websocket_endpoint)
    start = src.index("except WebSocketDisconnect")
    end = src.index("finally:", start)  # both except blocks precede the finally
    disconnect_region = src[start:end]
    assert "clear_presence" not in disconnect_region, (
        "An involuntary-disconnect branch clears presence — a network blip would "
        "hide the driver from riders for the reconnect window. Only revocation "
        "(heartbeat_task) and explicit Go Offline may clear."
    )


def test_go_offline_branch_still_clears_presence():
    """The online/offline toggle in routes/drivers.py must clear presence on the
    is_online=False branch specifically — not merely reference clear_presence
    somewhere in the module (subscription/expiry paths also call it)."""
    from backend.routes.drivers import status as drv_status

    src = inspect.getsource(drv_status)
    # Go Online marks present; the paired else (Go Offline) clears it.
    # (presence helpers are reached via the _deps seam since the god-file split)
    assert "await _deps.mark_present(driver_id)" in src, "go-online mark_present call missing"
    assert re.search(r"else:\s+await _deps\.clear_presence\(driver_id\)", src), (
        "the Go Offline branch (else of `if is_online:`) no longer clears the "
        "driver's presence key"
    )


def test_presence_ttl_is_the_grace_window():
    """Sanity-pin the TTL the network-drop grace relies on: 30s == 3x the 10s WS
    heartbeat (two missed pings). Shrinking it toward the heartbeat kills the
    grace; growing it unbounded lets a dead app linger too long."""
    from backend.utils.driver_presence import PRESENCE_TTL

    assert PRESENCE_TTL == 30
