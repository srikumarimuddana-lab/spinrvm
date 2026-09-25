"""decline_ride with reason "offer_expired" (driver-app countdown auto-decline).

Phase 0 of .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md.

The driver app posts a decline when the offer card's countdown reaches 0.
Handled as a decline, that reset the miss streak, so an unattended
foregrounded app never reached auto_offline_miss_threshold. With
settings.offer_expired_decline_as_miss_enabled (migration 466) on, decline_ride
leaves the offer pending for the server-side expiry path to count as a miss.

Patch targets follow test_driver_ride_flow_coverage.py's conventions.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_DRIVER_ID = "drv-exp-1"
_USER_ID = "user-exp-1"
_RIDE_ID = "ride-exp-1"


def _driver():
    return {"id": _DRIVER_ID, "user_id": _USER_ID, "status": "active", "is_online": True}


def _ride(status="searching", driver_id=None):
    return {"id": _RIDE_ID, "status": status, "driver_id": driver_id, "rider_id": "rider-exp-1"}


def _request(body):
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


def _get_rows(pending_offer_rows):
    """get_rows fake: the drivers lookup, then the pending ride_offers lookup."""

    async def _fake(table, filters=None, **_kw):
        if table == "drivers":
            return [_driver()]
        if table == "ride_offers":
            assert filters == {"ride_id": _RIDE_ID, "driver_id": _DRIVER_ID, "status": "pending"}
            return pending_offer_rows
        return []

    return AsyncMock(side_effect=_fake)


class _Env:
    """Patches every side effect of the legacy decline path so each test can
    assert which ones ran."""

    def __init__(self, *, flag, pending_offer_rows, ride, offer_update_rows=None, settings_error=None):
        self.flag = flag
        self.pending_offer_rows = pending_offer_rows
        self.ride = ride
        self.offer_update_rows = offer_update_rows if offer_update_rows is not None else []
        self.settings_error = settings_error

    def __enter__(self):
        self._stack = ExitStack()
        settings = (
            AsyncMock(side_effect=self.settings_error)
            if self.settings_error
            else AsyncMock(return_value={"offer_expired_decline_as_miss_enabled": self.flag})
        )
        self.get_rows = _get_rows(self.pending_offer_rows)
        self.run_sync = AsyncMock(return_value=MagicMock(data=self.offer_update_rows))
        self.acceptance = AsyncMock()
        self.release = AsyncMock(return_value=1)
        self.reset_streak = AsyncMock()
        self.audit = AsyncMock()
        self.redis_set = AsyncMock()
        for target, mock in (
            ("backend.settings_loader.get_app_settings", settings),
            ("backend.routes.drivers._deps.db_supabase.get_rows", self.get_rows),
            ("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=self.ride)),
            ("backend.routes.drivers._deps.db_supabase.run_sync", self.run_sync),
            # Admin-assigned revert (is_assigned and no offer row): None = nothing to revert.
            ("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=None)),
            ("backend.repositories.driver_repo.update_acceptance_rate", self.acceptance),
            ("backend.routes.drivers._deps.release_driver_and_close_period", self.release),
            ("backend.routes.drivers._deps.reset_miss_streak", self.reset_streak),
            ("backend.routes.drivers.ride_flow.reset_miss_streak", self.reset_streak),
            ("backend.routes.drivers._deps.db.insert_one", self.audit),
            ("backend.utils.redis_client.redis_set", self.redis_set),
            ("backend.routes.drivers._deps.spawn", MagicMock(side_effect=lambda coro: coro.close())),
            ("backend.routes.rides.match_driver_to_ride", AsyncMock()),
        ):
            self._stack.enter_context(patch(target, mock))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


async def _decline(body):
    from backend.routes.drivers.ride_flow import decline_ride

    return await decline_ride(ride_id=_RIDE_ID, request=_request(body), current_user={"id": _USER_ID})


async def test_flag_on_pending_offer_is_left_for_server_expiry():
    with _Env(flag=True, pending_offer_rows=[{"id": "offer-1"}], ride=_ride()) as env:
        result = await _decline({"reason": "offer_expired"})

    assert result == {"success": True, "outcome": "left_to_expire", "already_resolved": False}
    # None of the decline side effects ran: the offer row stays pending, the
    # streak is not reset, and no decline is recorded or penalised here.
    env.run_sync.assert_not_awaited()
    env.reset_streak.assert_not_awaited()
    env.acceptance.assert_not_awaited()
    env.release.assert_not_awaited()
    env.audit.assert_not_awaited()
    env.redis_set.assert_not_awaited()


async def test_flag_off_offer_expired_is_still_a_decline():
    with _Env(flag=False, pending_offer_rows=[{"id": "offer-1"}], ride=_ride(), offer_update_rows=[{"id": "o"}]) as env:
        result = await _decline({"reason": "offer_expired"})

    assert result == {"success": True}
    env.reset_streak.assert_awaited_once_with(_DRIVER_ID)
    env.acceptance.assert_awaited_once()
    env.release.assert_awaited_once()
    # Flag off never reads ride_offers for the auto-decline branch.
    assert all(call.args[0] != "ride_offers" for call in env.get_rows.await_args_list)


async def test_manual_decline_unchanged_with_flag_on():
    with _Env(flag=True, pending_offer_rows=[{"id": "offer-1"}], ride=_ride(), offer_update_rows=[{"id": "o"}]) as env:
        result = await _decline({})

    assert result == {"success": True}
    env.reset_streak.assert_awaited_once_with(_DRIVER_ID)
    env.audit.assert_awaited_once()


async def test_no_pending_offer_falls_through_to_existing_guard():
    from fastapi import HTTPException

    with _Env(flag=True, pending_offer_rows=[], ride=_ride(), offer_update_rows=[]) as env:
        with pytest.raises(HTTPException) as exc:
            await _decline({"reason": "offer_expired"})

    # Same 403 as today when this driver holds no pending offer and is not assigned.
    assert exc.value.status_code == 403
    env.reset_streak.assert_not_awaited()


async def test_assigned_driver_keeps_the_decline_path():
    # Admin direct-assignment / single-offer: the ride row holds this driver and
    # the decline path is what reverts it to searching. Not changed here.
    ride = _ride(status="driver_assigned", driver_id=_DRIVER_ID)
    with _Env(flag=True, pending_offer_rows=[{"id": "offer-1"}], ride=ride, offer_update_rows=[]) as env:
        result = await _decline({"reason": "offer_expired"})

    assert result == {"success": True}
    env.release.assert_awaited_once()
    assert all(call.args[0] != "ride_offers" for call in env.get_rows.await_args_list)


async def test_settings_read_failure_is_treated_as_flag_off():
    with _Env(
        flag=True,
        pending_offer_rows=[{"id": "offer-1"}],
        ride=_ride(),
        offer_update_rows=[{"id": "o"}],
        settings_error=RuntimeError("settings unavailable"),
    ) as env:
        result = await _decline({"reason": "offer_expired"})

    assert result == {"success": True}
    env.reset_streak.assert_awaited_once_with(_DRIVER_ID)
