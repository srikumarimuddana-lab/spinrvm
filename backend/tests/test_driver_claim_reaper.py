"""Tests for the orphaned driver-claim reaper (C3).

A driver claimed by dispatch (is_available=false) whose offer-insert never
landed (crash) must be released; a legitimately busy driver (pending offer or
active ride) or a recently-claimed one must NOT be.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _driver(minutes_ago=5, claimed=True, claim_id=None):
    stamp = None
    if claimed:
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": "drv_1",
        "user_id": "u_1",
        "is_online": True,
        "is_available": False,
        "availability_claimed_at": stamp,
        "availability_claim_id": claim_id,
    }


def _patches(*, drivers, result=None):
    P = "backend.utils.driver_claim_reaper."

    async def _get_rows(table, flt, **kw):
        if table == "drivers":
            return drivers
        return []

    release_mock = AsyncMock(return_value=result or {"status": "released"})
    return [
        patch(P + "db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(P + "reap_stale_driver_claim", release_mock),
    ], release_mock


@pytest.mark.asyncio
class TestReapTick:
    async def test_orphan_is_released(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        drivers = [_driver(minutes_ago=5)]
        patches, release = _patches(drivers=drivers)
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_awaited_once_with("drv_1", None, drivers[0]["availability_claimed_at"])

    async def test_recent_claim_not_reaped(self):
        """Within the threshold (sub-second claim→insert window) → never touched."""
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        drivers = [_driver(minutes_ago=0)]
        patches, release = _patches(drivers=drivers, result={"status": "claim_too_recent"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_awaited_once()

    async def test_null_stamp_not_reaped(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        drivers = [_driver(claimed=False)]
        patches, release = _patches(drivers=drivers, result={"status": "claim_identity_or_stamp_missing"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_awaited_once_with("drv_1", None, None)

    async def test_pending_offer_not_reaped(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)], result={"status": "offer_active"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_awaited_once()

    async def test_active_ride_not_reaped(self):
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)], result={"status": "ride_active"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_awaited_once()

    async def test_stale_pending_offer_left_to_offer_reaper(self):
        """Offer expiry is owned by offer_expiry_reaper, which expires stale
        offers via process_expired_offer with the full miss-streak /
        acceptance-rate / insurance-period / re-dispatch side-effects. The claim
        reaper must NOT expire offers itself (a bare status flip would skip those
        side-effects, e.g. leave no insurance Period-1 row). So a driver with any
        pending offer — even a stale one — is treated as busy and left alone; the
        offer reaper clears it and releases the driver with correct accounting."""
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)], result={"status": "offer_active"})
        expire = AsyncMock()
        patches.append(patch("backend.utils.driver_claim_reaper.db.update_one", expire))
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        expire.assert_not_awaited()  # claim reaper no longer expires offers
        release.assert_awaited_once()  # DB leaves the active offer untouched

    async def test_fresh_pending_offer_still_blocks(self):
        """A pending offer is a live claim → the driver is not reaped."""
        from contextlib import ExitStack

        from backend.utils.driver_claim_reaper import _reap_tick

        patches, release = _patches(drivers=[_driver(minutes_ago=5)], result={"status": "offer_active"})
        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _reap_tick()

        release.assert_awaited_once()
