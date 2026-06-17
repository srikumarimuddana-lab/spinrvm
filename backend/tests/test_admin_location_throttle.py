"""#3: admin driver-location fan-out is throttled per driver.

Ride-status events stay unthrottled (broadcast_to_admins); only the ~1 Hz
location pings are rate-limited so they don't fan out N drivers x A admins/sec.
"""

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def test_location_broadcast_throttled_per_driver():
    import backend.socket_manager as sm
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    sent: list = []

    async def _fake_broadcast(msg):
        sent.append(msg)

    mgr.broadcast_to_admins = _fake_broadcast  # type: ignore[method-assign]

    clock = {"v": 1000.0}
    with patch("backend.socket_manager.time.monotonic", lambda: clock["v"]):
        asyncio.run(mgr.broadcast_driver_location_to_admins("d1", {"n": 1}))  # sent
        asyncio.run(mgr.broadcast_driver_location_to_admins("d1", {"n": 2}))  # throttled (same instant)
        asyncio.run(mgr.broadcast_driver_location_to_admins("d2", {"n": 3}))  # sent (different driver)
        clock["v"] += sm._ADMIN_LOC_MIN_INTERVAL + 0.1
        asyncio.run(mgr.broadcast_driver_location_to_admins("d1", {"n": 4}))  # sent (interval elapsed)

    assert [m["n"] for m in sent] == [1, 3, 4]
