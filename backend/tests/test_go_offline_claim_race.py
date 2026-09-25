"""Go Offline must not land on top of a concurrent dispatch claim (v1 path).

`update_driver_status` checks for an active ride and a fresh pending offer,
then writes `is_online=false` later in the handler. `claim_driver_atomic` can
land in between: it flips `is_available` true -> false and stamps
`availability_claimed_at` before the `ride_offers` row exists. Before this fix
the offline write was unconditional, so an obligated driver could be taken
offline (and recorded in Period 0) with an offer live.

Now:
- the online -> offline write is conditional on `is_available` still being
  what the checks saw; a claim in between matches zero rows and the verify
  read turns that into a 409, with no insurance-period write;
- a claim already in flight when the request arrives (is_available=false,
  fresh availability_claimed_at, no offer row yet) is a 409 up front;
- an old unreleased claim is an orphan for the claim reaper and does not
  block going offline.
Found by the Codex review of PR #5775.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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


def _driver(*, is_online: bool, is_available, claimed_seconds_ago: float | None = None) -> dict:
    row = {
        "id": DRIVER_ID,
        "user_id": "u1",
        "status": "active",
        "is_verified": True,
        "is_online": is_online,
        "is_available": is_available,
        "service_area_id": None,
    }
    if claimed_seconds_ago is not None:
        row["availability_claimed_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=claimed_seconds_ago)
        ).isoformat()
    return row


async def _get_rows(table, filters=None, **kw):
    # No active ride and no pending offer: the checks pass, as they would in
    # the gap between a claim and its ride_offers insert.
    if table in (
        "rides",
        "ride_offers",
        "driver_documents",
        "service_areas",
        "settings",
        "app_settings",
        "legal_documents",
    ):
        return []
    raise AssertionError(f"unexpected table {table}")


async def _settings():
    return {}


async def _run(pre: dict, verify: dict):
    update_one = AsyncMock(return_value={"id": DRIVER_ID})
    record_period = AsyncMock()
    with (
        patch.object(status_mod.db_supabase, "get_driver_by_id", AsyncMock(side_effect=[pre, verify])),
        patch.object(status_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(status_mod.db_supabase, "update_one", update_one),
        patch.object(status_mod._deps, "record_period_transition", record_period),
        patch.object(status_mod._deps, "mark_present", AsyncMock()),
        patch.object(status_mod._deps, "clear_presence", AsyncMock()),
        patch.object(status_mod, "reset_miss_streak", AsyncMock()),
        patch("utils.dual_run_monitor.record_go_online_flip", AsyncMock()),
        patch.object(settings_loader, "get_app_settings", _settings),
    ):
        try:
            result = await status_mod.update_driver_status(
                driver_id=DRIVER_ID, is_online=False, lat=None, lng=None, current_user=USER
            )
            error = None
        except HTTPException as exc:
            result, error = None, exc
    return result, error, update_one, record_period


class TestOfflineWriteIsConditional:
    @pytest.mark.anyio
    async def test_offline_write_filters_on_the_is_available_the_checks_saw(self):
        pre = _driver(is_online=True, is_available=True)
        _, error, update_one, _ = await _run(pre, _driver(is_online=False, is_available=False))

        assert error is None
        filters = update_one.await_args_list[0].args[1]
        assert filters == {"id": DRIVER_ID, "is_available": True}

    @pytest.mark.anyio
    async def test_claim_between_checks_and_write_is_a_409_not_an_offline_driver(self):
        """The conditional write matched nothing because a claim flipped
        is_available; the row is still online with is_available=false."""
        pre = _driver(is_online=True, is_available=True)
        claimed = _driver(is_online=True, is_available=False, claimed_seconds_ago=0)
        _, error, _, record_period = await _run(pre, claimed)

        assert error is not None and error.status_code == 409
        assert "offer" in str(error.detail).lower()
        # No Period 0 row for a driver who is now obligated to an offer.
        record_period.assert_not_awaited()

    @pytest.mark.anyio
    async def test_offline_reassert_by_an_offline_driver_keeps_the_plain_filter(self):
        # Not a flip: nothing for a claim to race, and a stale flag must not
        # turn a re-assert into a conditional write that can miss.
        pre = _driver(is_online=False, is_available=False)
        _, error, update_one, _ = await _run(pre, pre)

        assert error is None
        assert update_one.await_args_list[0].args[1] == {"id": DRIVER_ID}


class TestClaimInFlight:
    @pytest.mark.anyio
    async def test_fresh_unreleased_claim_blocks_go_offline_before_any_write(self):
        pre = _driver(is_online=True, is_available=False, claimed_seconds_ago=5)
        _, error, update_one, _ = await _run(pre, pre)

        assert error is not None and error.status_code == 409
        update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_old_unreleased_claim_is_an_orphan_and_does_not_block(self):
        pre = _driver(is_online=True, is_available=False, claimed_seconds_ago=status_mod.CLAIM_IN_FLIGHT_SECONDS + 60)
        _, error, update_one, _ = await _run(pre, _driver(is_online=False, is_available=False))

        assert error is None
        assert update_one.await_args_list[0].args[1] == {"id": DRIVER_ID, "is_available": False}

    @pytest.mark.anyio
    async def test_available_driver_with_a_stale_stamp_is_not_in_flight(self):
        # Released claims clear the stamp; a leftover one on an available
        # driver still must not count.
        pre = _driver(is_online=True, is_available=True, claimed_seconds_ago=1)
        _, error, _, _ = await _run(pre, _driver(is_online=False, is_available=False))

        assert error is None
