"""
Unit tests for the is_available ⇒ is_online invariant in
repositories/driver_repo.py :: set_driver_available.

set_driver_available is called from ride-release paths (cancel, complete) with
available=True. It must never mark an offline driver available — is_online is
driver-toggled and must not be flipped on here — so when asked to make a
driver available it clamps is_available to the driver's current online state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


def _supabase_with_online(is_online: bool, captured: dict, total_rides: int = 5):
    """Build a fluent supabase mock. select(...) returns the row's online
    state; update(...) records the payload it was given."""

    def _update(payload):
        captured["payload"] = payload
        upd = MagicMock()
        upd.eq.return_value.execute.return_value = {"data": [{**payload, "id": "d1", "user_id": "u1"}]}
        return upd

    tbl = MagicMock()
    tbl.select.return_value.eq.return_value.execute.return_value = {
        "data": [{"is_online": is_online, "total_rides": total_rides}]
    }
    tbl.update.side_effect = _update

    sb = MagicMock()
    sb.table.return_value = tbl
    return sb


async def _passthrough_run_sync(func, *_a, **_k):
    return func()


async def _call_set_available(is_online: bool, captured: dict, **kwargs):
    from backend.repositories import driver_repo

    sb = _supabase_with_online(is_online, captured)
    with (
        patch.object(driver_repo, "supabase", sb),
        patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()),
    ):
        return await driver_repo.set_driver_available("d1", available=True, **kwargs)


async def test_offline_driver_not_marked_available():
    """available=True on an offline driver clamps is_available to False."""
    captured: dict = {}
    await _call_set_available(is_online=False, captured=captured)
    assert captured["payload"]["is_available"] is False
    # is_online must never be written by this function (driver-toggled).
    assert "is_online" not in captured["payload"]


async def test_online_driver_marked_available():
    """available=True on an online driver stays available."""
    captured: dict = {}
    await _call_set_available(is_online=True, captured=captured)
    assert captured["payload"]["is_available"] is True
    assert "is_online" not in captured["payload"]


async def test_total_rides_increment_still_clamps_when_offline():
    """The total_rides increment path also enforces the invariant."""
    captured: dict = {}
    await _call_set_available(is_online=False, captured=captured, total_rides_inc=1)
    assert captured["payload"]["is_available"] is False
    assert captured["payload"]["total_rides"] == 6
