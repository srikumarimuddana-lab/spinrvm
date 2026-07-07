"""Presence must survive an *involuntary* WebSocket disconnect.

Background
----------
A driver appears in the rider app (`/drivers/nearby` map pins and
`/rides/estimate`) only while a live Redis *presence* key exists for them;
admin instead reads the durable `drivers.is_online` column. The presence key
carries a 30s TTL (utils/driver_presence.PRESENCE_TTL) precisely so a flaky
mobile connection — socket drops, client reconnects a couple of seconds later
— does NOT yank the driver out of rider results for the length of the blip.

The regression this pins
------------------------
The WebSocket disconnect handler used to call `clear_presence(driver_id)` on
every involuntary `WebSocketDisconnect` / error. On a flapping connection that
wiped the key seconds after each reconnect, so the driver flickered offline to
riders while admin still showed them online (the reported "car online in admin
but not in the rider app"). The fix removes `clear_presence` from the
involuntary-disconnect branches and lets the TTL lapse (the presence sweeper
reconciles `is_online` for genuinely-dead apps). The *explicit* Go Offline path
in routes/drivers.py must still clear immediately.

Because the disconnect logic lives inline inside the large
`websocket_endpoint` coroutine (not a separately-callable unit), these tests
pin the contract at module level: the websocket module must not be able to
clear presence, and the drivers module (Go Offline) still can.
"""

from __future__ import annotations

import inspect


def test_websocket_module_does_not_clear_presence_on_disconnect():
    """The websocket handler must NOT reference clear_presence anywhere.

    An involuntary WebSocketDisconnect/error must leave the presence key alone
    so the 30s TTL can absorb a reconnect. If a future change re-imports or
    re-invokes clear_presence in this module, that grace is defeated and this
    test fails on purpose — routing the author back to this rationale.
    """
    from backend.routes import websocket as ws_mod

    # Not bound in the module namespace (import was dropped).
    assert not hasattr(ws_mod, "clear_presence"), (
        "clear_presence is bound in routes.websocket — an involuntary disconnect "
        "could wipe the driver's presence key and hide them from riders during a "
        "network blip. Let the 30s TTL lapse instead."
    )

    # And the source text of the endpoint doesn't call it either (guards against
    # a fully-qualified call that wouldn't create a module-level binding).
    source = inspect.getsource(ws_mod)
    assert "clear_presence(" not in source, (
        "routes.websocket calls clear_presence(...) — the involuntary-disconnect "
        "branches must not clear presence; only explicit Go Offline may."
    )


def test_go_offline_path_still_clears_presence():
    """The explicit Go Offline path (routes/drivers.py) MUST still clear the
    presence key immediately — a driver who taps 'Go Offline' should drop out
    of dispatch and rider results at once, not linger for the TTL window."""
    from backend.routes import drivers as drv_mod

    source = inspect.getsource(drv_mod)
    assert "clear_presence(" in source, (
        "routes.drivers no longer calls clear_presence — an explicit Go Offline "
        "would leave a stale presence key for up to the 30s TTL."
    )


def test_presence_ttl_is_the_grace_window():
    """Sanity-pin the TTL the disconnect grace relies on. If this shrinks toward
    the heartbeat interval the grace disappears; if it grows unbounded a dead app
    lingers too long. 30s == 3x the 10s WS heartbeat (two missed pings)."""
    from backend.utils.driver_presence import PRESENCE_TTL

    assert PRESENCE_TTL == 30
