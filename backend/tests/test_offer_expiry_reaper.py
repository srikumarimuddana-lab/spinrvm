"""Tests for the durable offer-expiry reaper (issue #9).

The reaper is the restart-safe backstop for offer timeouts: it finds pending
ride_offers past their persisted expires_at and runs the same idempotent
process_expired_offer the in-process handler uses, then re-dispatches affected
still-searching rides. Exactly-once processing is guaranteed by
process_expired_offer's atomic claim (covered by test_offer_timeout); these
tests cover the reaper's own query, per-offer fan-out, and re-dispatch guard.

Uses patch.object on the imported module so patching is robust to the repo's
backend./non-backend dual-import module identity.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import offer_expiry_reaper as reaper


@pytest.mark.asyncio
async def test_reap_tick_expires_offers_and_redispatches_searching_ride():
    expired = [
        {"ride_id": "r1", "driver_id": "d1"},
        {"ride_id": "r1", "driver_id": "d2"},
    ]
    captured = {}

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            captured["offers_filter"] = filt
            return expired
        if table == "rides":
            return [{"status": "searching"}]
        return []

    proc = AsyncMock(return_value=True)
    redispatch = AsyncMock()

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", redispatch),
    ):
        await reaper._reap_tick()

    # Scans only pending offers past their deadline.
    assert captured["offers_filter"].get("status") == "pending"
    assert "$lt" in captured["offers_filter"].get("expires_at", {})
    # Each expired offer processed via the shared idempotent function.
    assert proc.await_count == 2
    proc.assert_any_await("r1", "d1", 3)
    proc.assert_any_await("r1", "d2", 3)
    # The affected searching ride is re-dispatched exactly once (deduped by ride).
    redispatch.assert_awaited_once_with("r1")


@pytest.mark.asyncio
async def test_reap_tick_no_expired_offers_is_a_noop():
    proc = AsyncMock()
    redispatch = AsyncMock()
    with (
        patch.object(reaper.db, "get_rows", AsyncMock(return_value=[])),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", redispatch),
    ):
        await reaper._reap_tick()

    proc.assert_not_awaited()
    redispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_reap_tick_does_not_redispatch_a_non_searching_ride():
    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return [{"ride_id": "r1", "driver_id": "d1"}]
        if table == "rides":
            # Ride was accepted between offer dispatch and this reap tick.
            return [{"status": "driver_accepted"}]
        return []

    proc = AsyncMock(return_value=True)
    redispatch = AsyncMock()
    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", redispatch),
    ):
        await reaper._reap_tick()

    proc.assert_awaited_once_with("r1", "d1", 3)
    redispatch.assert_not_awaited()
