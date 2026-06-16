"""Tests for the orphaned driver-claim reaper (C3).

A driver claimed by dispatch (is_available=false) whose offer-insert never
landed (crash) must be released; a legitimately busy driver (pending offer or
active ride) or a recently-claimed one must NOT be.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _driver(minutes_ago=5, claimed=True):
    stamp = None
    if claimed:
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": "drv_1",
        "user_id": "u_1",
        "is_online": True,
        "is_available": False,
        "availability_claimed_at": stamp,
    }


def _patches(*, drivers, pending_offer=False, active_ride=False, release_ok=True):
    P = "backend.utils.driver_claim_reaper."

    async def _get_rows(table, flt, **kw):
        if table == "drivers":
            return drivers
        if table == "ride_offers":
            return [{"id": "o1"}] if pending_offer else []
        if table == "rides":
            return [{"id": "r1"}] if active_ride else []
        return []

    release_mock = AsyncMock(return_value={"is_available": True} if release_ok else {"is_available": False})
    return [
        patch(P + "db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(P + "set_driver_available", release_mock),
    ], release_mock


@pytest.mark.asyncio
class TestReapTick:
    async def test_orphan_is_released(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)])
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_called_once_with("drv_1", available=True)

    async def test_recent_claim_not_reaped(self):
        """Within the threshold (sub-second claim→insert window) → never touched."""
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=0)])  # claimed seconds ago
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_not_called()

    async def test_null_stamp_not_reaped(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(claimed=False)])
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_not_called()

    async def test_pending_offer_not_reaped(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)], pending_offer=True)
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_not_called()

    async def test_active_ride_not_reaped(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)], active_ride=True)
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_not_called()
