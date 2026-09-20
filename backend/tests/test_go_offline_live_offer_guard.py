"""Go Offline 409 guard extended to a live batch-dispatch offer.

Companion to `test_driver_status_live_offer_period.py`, which fixed the
*insurance audit record* for this same gap without changing driver-visible
behaviour. This file covers the other half: whether the Go Offline toggle
itself is rejected while a batch-dispatch offer is live.

The gap
-------
`routes/drivers/status.py`'s Go Offline 409 guard queries `rides` for an
active row, and its own comment says "To go offline during an offer,
decline it first." But batch dispatch holds its claim in `ride_offers`
with no `rides.driver_id` link pre-acceptance, so a driver mid-batch-offer
was never actually caught by that guard — nothing enforced the rule the
comment states.

Flag
----
Gated on `go_offline_live_offer_guard_enabled` (CLAUDE.md release gate #3,
default OFF) — a SEPARATE flag from `insurance_period_live_offer_enabled`,
because this one changes what a driver can do (a previously-allowed toggle
now 409s), not just what gets written to the insurance audit trail. Both
states are pinned: OFF must reproduce the old (bug-included) behaviour —
including making zero extra `ride_offers` query, not just returning the
same result — ON must reject the toggle.

Corrected during review
------------------------
`spinr-dispatch-reviewer` (CLAUDE.md gate 10) found that the guard as first
written ran on every `is_online=False` request, including an idempotent
re-assert from a driver already offline in the DB (connection drop, admin
force-offline, app relaunch) — a driver-stuck scenario, since a stale/
orphaned `ride_offers` row is invisible to the driver and they cannot clear
it. The guard now only runs on a genuine online -> offline FLIP;
`TestIdempotentReassertIsNeverBlocked` pins that.
"""

from __future__ import annotations

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


def _live_offer(offered_at: str | None = None) -> dict:
    # No `offered_at` -> `_fresh_pending_offers` treats it as fresh (that
    # helper's documented "never grant availability on ambiguity" rule).
    offer = {"id": "offer-1", "ride_id": OFFER_RIDE_ID}
    if offered_at is not None:
        offer["offered_at"] = offered_at
    return offer


def _stale_offer() -> dict:
    # Well past STALE_PENDING_OFFER_SECONDS (90s) -> _fresh_pending_offers
    # must exclude it. An orphaned claim must not block the toggle.
    return {"id": "offer-old", "ride_id": "ride-orphaned", "offered_at": "2020-01-01T00:00:00Z"}


def _patches(*, current_online: bool, offers: list, flag_on: bool, ride_offers_calls: list):
    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return []  # batch dispatch: no rides.driver_id link pre-acceptance
        if table == "ride_offers":
            ride_offers_calls.append((filters, kw))
            filters = filters or {}
            # Both the status AND the driver_id must be right for this to
            # match — a filter drifting on either must not silently keep
            # returning offers for the wrong driver or the wrong state.
            if filters.get("status") != "pending" or filters.get("driver_id") != DRIVER_ID:
                return []
            return offers
        if table in ("driver_documents", "service_areas", "settings", "app_settings"):
            return []
        raise AssertionError(f"unexpected table {table}")

    async def _settings():
        return {"go_offline_live_offer_guard_enabled": flag_on}

    return (
        patch.object(
            status_mod.db_supabase,
            "get_driver_by_id",
            # Go Offline that 409s never reaches the post-write verify read —
            # a single pre-write row is all the guard needs.
            AsyncMock(side_effect=[_driver(current_online), _driver(False)]),
        ),
        patch.object(status_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(status_mod.db_supabase, "update_one", AsyncMock(return_value={"id": DRIVER_ID})),
        patch.object(status_mod._deps, "record_period_transition", AsyncMock()),
        patch.object(status_mod._deps, "mark_present", AsyncMock()),
        patch.object(status_mod._deps, "clear_presence", AsyncMock()),
        patch.object(status_mod, "reset_miss_streak", AsyncMock()),
        patch("utils.dual_run_monitor.record_go_online_flip", AsyncMock()),
        patch.object(settings_loader, "get_app_settings", _settings),
    )


async def _go_offline():
    return await status_mod.update_driver_status(
        driver_id=DRIVER_ID, is_online=False, lat=None, lng=None, current_user=USER
    )


async def _run(*, current_online: bool, offers: list, flag_on: bool):
    calls: list = []
    ps = _patches(current_online=current_online, offers=offers, flag_on=flag_on, ride_offers_calls=calls)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
        result = await _go_offline()
    return result, calls


async def _run_expect_409(*, current_online: bool, offers: list, flag_on: bool):
    calls: list = []
    ps = _patches(current_online=current_online, offers=offers, flag_on=flag_on, ride_offers_calls=calls)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
        with pytest.raises(HTTPException) as exc:
            await _go_offline()
    return exc.value, calls


class TestFlagOnRejectsGoOfflineDuringLiveOffer:
    @pytest.mark.anyio
    async def test_live_offer_409s_the_go_offline_toggle(self):
        """The core fix: the driver cannot go offline while holding a live
        batch-dispatch offer — matching the guard's own stated intent."""
        exc, _ = await _run_expect_409(current_online=True, offers=[_live_offer()], flag_on=True)
        assert exc.status_code == 409
        assert "pending ride offer" in exc.detail

    @pytest.mark.anyio
    async def test_no_offer_still_allows_go_offline(self):
        """The fix must not touch the ordinary, offer-free toggle."""
        result, _ = await _run(current_online=True, offers=[], flag_on=True)
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_stale_offer_does_not_block_go_offline(self):
        """An orphaned claim past the 90s staleness window must not park a
        driver who is trying to go offline — same rule as the online-path
        `_fresh_pending_offers` callers, exercised here specifically for
        this call site rather than assumed from the shared helper."""
        result, _ = await _run(current_online=True, offers=[_stale_offer()], flag_on=True)
        assert result["success"] is True


class TestIdempotentReassertIsNeverBlocked:
    """A driver already offline in the DB (connection drop, admin
    force-offline, app relaunch) re-tapping "Go offline" is a no-op, not a
    flip. It must never 409, regardless of flag state or any stale/orphaned
    `ride_offers` row the driver has no way to see or clear."""

    @pytest.mark.anyio
    async def test_already_offline_with_live_offer_and_flag_on_is_not_blocked(self):
        result, calls = await _run(current_online=False, offers=[_live_offer()], flag_on=True)
        assert result["success"] is True
        assert calls == [], "not a flip -> the guard must not even query ride_offers"


class TestFlagOffIsUnchanged:
    """OFF is what production runs until an operator flips it, so the old
    behaviour — the driver CAN go offline mid-offer — must be reproduced
    exactly, including not spending the extra `ride_offers` read."""

    @pytest.mark.anyio
    async def test_live_offer_still_allows_go_offline(self):
        result, _ = await _run(current_online=True, offers=[_live_offer()], flag_on=False)
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_flag_off_makes_no_extra_ride_offers_query(self):
        """Not just 'same result' — OFF must cost nothing extra either."""
        _, calls = await _run(current_online=True, offers=[_live_offer()], flag_on=False)
        assert calls == [], "flag OFF must not query ride_offers on the offline path at all"
