"""Insurance-period correctness while a driver holds a live dispatch offer.

Companion to `test_driver_status_insurance_periods.py`, which covers the
ride-row cases. This file covers the gap that file's guard cannot reach.

The gap
-------
`routes/drivers/status.py`'s Go Offline guard rejects the toggle when the
driver has an active `rides` row. Batch dispatch, however, holds its claim
in `ride_offers` and sets no `rides.driver_id` until the driver accepts —
so a driver mid-offer passes that guard. Before the fix the handler then
recorded Period 0 (offline) or Period 1 (online), overwriting the correct
Period 2 that `routes/rides/matching.py` opened at claim time.

CLAUDE.md is explicit that this is wrong: "Period 2 starts on
`driver_assigned` (not `driver_accepted`) — a driver becomes obligated to
the ride the instant `claim_driver_atomic` succeeds and the offer is
live." Recording Period 0 there says "personal auto only" for a driver
already carrying a commercial obligation.

Flag
----
The fix is gated on `insurance_period_live_offer_enabled` (CLAUDE.md
release gate #3, default OFF). Both states are pinned here: OFF must
reproduce the old behaviour exactly, ON must preserve Period 2. Testing
only the ON path would let a broken OFF path ship silently to every
driver, since OFF is what production runs until an operator flips it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.routes.drivers import status as status_mod
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    from routes.drivers import status as status_mod  # type: ignore

try:
    from backend import settings_loader
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    import settings_loader  # type: ignore

USER = {"id": "u1"}
DRIVER_ID = "drv-1"
OFFER_RIDE_ID = "ride-offered-1"


def _driver(is_online: bool) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": "u1",
        "status": "active",
        "is_verified": True,
        "is_online": is_online,
        "service_area_id": None,
    }


def _live_offer() -> dict:
    # No `offered_at` -> `_fresh_pending_offers` treats it as fresh, which is
    # that helper's documented "never grant availability on ambiguity" rule.
    return {"id": "offer-1", "ride_id": OFFER_RIDE_ID}


def _patches(*, current_online: bool, requested_online: bool, offers: list, flag_on: bool):
    period_mock = AsyncMock()

    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return []  # batch dispatch: no rides.driver_id link pre-acceptance
        if table == "ride_offers":
            return offers
        if table in ("driver_documents", "service_areas", "settings", "app_settings", "legal_documents"):
            return []
        raise AssertionError(f"unexpected table {table}")

    async def _settings():
        return {"insurance_period_live_offer_enabled": flag_on}

    return (
        period_mock,
        (
            patch.object(
                status_mod.db_supabase,
                "get_driver_by_id",
                AsyncMock(side_effect=[_driver(current_online), _driver(requested_online)]),
            ),
            patch.object(status_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(status_mod.db_supabase, "update_one", AsyncMock(return_value={"id": DRIVER_ID})),
            patch.object(status_mod._deps, "record_period_transition", period_mock),
            patch.object(status_mod._deps, "mark_present", AsyncMock()),
            patch.object(status_mod._deps, "clear_presence", AsyncMock()),
            patch.object(status_mod, "reset_miss_streak", AsyncMock()),
            patch("utils.dual_run_monitor.record_go_online_flip", AsyncMock()),
            # status.py imports get_app_settings lazily from the module, so
            # patching the module attribute is what the handler actually sees.
            patch.object(settings_loader, "get_app_settings", _settings),
        ),
    )


async def _toggle(is_online: bool):
    return await status_mod.update_driver_status(
        driver_id=DRIVER_ID, is_online=is_online, lat=None, lng=None, current_user=USER
    )


async def _run(*, current_online, requested_online, offers, flag_on):
    period_mock, ps = _patches(
        current_online=current_online,
        requested_online=requested_online,
        offers=offers,
        flag_on=flag_on,
    )
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
        result = await _toggle(requested_online)
    return period_mock, result


class TestFlagOnPreservesPeriodTwo:
    @pytest.mark.anyio
    async def test_go_offline_during_a_live_offer_is_rejected(self):
        """A pending offer blocks the offline toggle before any period write.
        The Period 2 row opened at claim time is left in place."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await _run(current_online=True, requested_online=False, offers=[_live_offer()], flag_on=True)
        assert exc.value.status_code == 409

    @pytest.mark.anyio
    async def test_go_online_during_a_live_offer_records_period_2(self):
        period_mock, _ = await _run(current_online=False, requested_online=True, offers=[_live_offer()], flag_on=True)
        period_mock.assert_awaited_once_with(DRIVER_ID, 2, ride_id=OFFER_RIDE_ID)

    @pytest.mark.anyio
    async def test_no_offer_still_records_plain_online_and_offline(self):
        """The fix must not disturb the ordinary rideless toggle."""
        online_mock, _ = await _run(current_online=False, requested_online=True, offers=[], flag_on=True)
        online_mock.assert_awaited_once_with(DRIVER_ID, 1, ride_id=None)

        offline_mock, _ = await _run(current_online=True, requested_online=False, offers=[], flag_on=True)
        offline_mock.assert_awaited_once_with(DRIVER_ID, 0, ride_id=None)


class TestFlagOffIsUnchanged:
    """OFF is what production runs until an operator flips it, so the old
    behaviour — bug included — must be reproduced exactly."""

    @pytest.mark.anyio
    async def test_go_offline_during_a_live_offer_is_rejected_before_period_write(self):
        """Going offline during a pending offer 409s before any period row.
        The insurance-period flag does not reopen that toggle."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await _run(current_online=True, requested_online=False, offers=[_live_offer()], flag_on=False)
        assert exc.value.status_code == 409

    @pytest.mark.anyio
    async def test_go_online_during_a_live_offer_still_records_period_1(self):
        period_mock, _ = await _run(current_online=False, requested_online=True, offers=[_live_offer()], flag_on=False)
        period_mock.assert_awaited_once_with(DRIVER_ID, 1)
