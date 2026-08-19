"""
C2 regression: PUT /drivers/{id}/status must never re-assert
is_available=True for a driver who is mid-trip or offer-pending.

is_online is driver intent; is_available is system-computed
(is_online AND no active ride AND no pending offer) — see the
update_driver_status docstring. The old handler wrote
is_available = is_online unconditionally, so a mid-trip "Go online" re-tap
(or an app-relaunch re-assert) put a busy driver straight back into the
dispatch pool — dispatch filters candidates on is_available alone
(services/dispatch_service.py), so that meant a second concurrent ride
offered to a driver who already has one.

Run:
    pytest backend/tests/test_go_online_availability.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

DRIVER_USER_ID = "driver_user_c2_avail"
DRIVER_ID = "driver_row_c2_avail"
RIDE_ID = "ride_c2_avail_001"


def _driver_row(**extra) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Driver C2",
        "rating": 4.9,
        "total_rides": 200,
        "is_online": True,
        "is_available": False,
        "status": "active",
        "is_verified": True,
        # SK eligibility re-check defaults (ranked audit #10): a plain
        # Class 5 licence and a fresh vehicle year, so existing tests that
        # don't care about eligibility keep passing unchanged.
        "license_class": "5",
        "vehicle_year": datetime.now(timezone.utc).year,
        **extra,
    }


def _active_ride(status: str) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": "rider_c2_avail",
        "driver_id": DRIVER_ID,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.e2e
# No explicit async marker: asyncio_mode=auto handles these. An explicit
# @pytest.mark.asyncio (or anyio) on a class routes through pytest-asyncio
# 0.23's legacy get_event_loop() wrapper, which blows up order-dependently
# once any earlier test leaves the main thread without a set event loop.
class TestGoOnlineAvailability:
    """Code under test: backend/routes/drivers.py::update_driver_status."""

    async def _go_online(self, rides_result, offers_result, expect_available):
        from backend.routes import drivers as drv_mod

        driver = _driver_row()
        updated = {**driver, "is_online": True, "is_available": expect_available}
        writes = []

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "rides":
                return list(rides_result)
            if table == "ride_offers":
                return list(offers_result)
            return []

        async def _update_one(table, filters, update):
            writes.append(update.get("$set", update))
            return updated

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
                AsyncMock(side_effect=[driver, updated]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
        ):
            result = await drv_mod.update_driver_status(
                driver_id=DRIVER_ID,
                is_online=True,
                current_user={"id": DRIVER_USER_ID},
            )
        return result, writes

    async def test_go_online_mid_trip_does_not_reassert_availability(self):
        """Re-tapping Go Online during an in_progress trip must keep the
        driver out of the dispatch pool."""
        result, writes = await self._go_online(
            rides_result=[_active_ride("in_progress")],
            offers_result=[],
            expect_available=False,
        )
        assert result["success"] is True
        assert writes, "driver row was not written"
        payload = writes[0]
        assert payload["is_online"] is True
        assert payload["is_available"] is False, (
            "mid-trip go-online must not put the driver back into the dispatch pool"
        )

    async def test_go_online_while_assigned_does_not_reassert_availability(self):
        """driver_assigned counts as busy — the driver is already obligated
        to the ride (same boundary as insurance Period 2)."""
        _, writes = await self._go_online(
            rides_result=[_active_ride("driver_assigned")],
            offers_result=[],
            expect_available=False,
        )
        assert writes[0]["is_available"] is False

    async def test_go_online_offer_pending_does_not_reassert_availability(self):
        """A pending batch offer holds no ride row — the claim lives in
        ride_offers. Going online mid-offer must not undo claim_driver."""
        _, writes = await self._go_online(
            rides_result=[],
            offers_result=[{"id": "offer-1", "driver_id": DRIVER_ID, "status": "pending"}],
            expect_available=False,
        )
        assert writes[0]["is_available"] is False

    async def test_go_online_idle_driver_is_available(self):
        """No active ride, no pending offer → the normal happy path still
        makes the driver available for dispatch."""
        result, writes = await self._go_online(
            rides_result=[],
            offers_result=[],
            expect_available=True,
        )
        assert result["success"] is True
        payload = writes[0]
        assert payload["is_online"] is True
        assert payload["is_available"] is True

    async def test_go_online_stale_pending_offer_is_ignored(self):
        """An orphaned pending offer (older than STALE_PENDING_OFFER_SECONDS,
        i.e. its ~15s timeout task died with its process) must NOT park the
        driver: the claim reaper expires the row; go-online ignores it.
        Fresh offers still block (see the offer_pending test above)."""
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        result, writes = await self._go_online(
            rides_result=[],
            offers_result=[{"id": "offer-stale", "driver_id": DRIVER_ID, "status": "pending", "offered_at": stale}],
            expect_available=True,
        )
        assert result["success"] is True
        assert writes[0]["is_available"] is True, (
            "a stale orphaned offer must not keep the driver out of the dispatch pool"
        )
        assert len(writes) == 1, "stale offer must not trigger the post-write repair either"

    async def test_claim_landing_mid_write_is_repaired(self):
        """dispatch_service.claim_driver() can fire between this handler's
        pre-write busy/offer reads and its row write; the write then stomps
        the claim (is_available back to True) and the driver re-enters the
        dispatch pool while already offered a ride. The post-write re-check
        must catch the claim and downgrade with claim_driver's conditional
        shape ({id, is_available: True}) so the repair itself can never
        stomp a newer state."""
        from backend.routes import drivers as drv_mod

        driver = _driver_row()
        updated = {**driver, "is_online": True, "is_available": True}
        writes: list[tuple[dict, dict]] = []
        offer_calls = {"n": 0}

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            if table == "ride_offers":
                offer_calls["n"] += 1
                if offer_calls["n"] == 1:
                    return []  # pre-write check: no claim yet
                # post-write re-check: a claim landed in the gap
                return [{"id": "offer-race", "driver_id": DRIVER_ID, "status": "pending"}]
            return []

        async def _update_one(table, filters, update):
            writes.append((filters, update.get("$set", update)))
            return updated

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
                AsyncMock(side_effect=[driver, updated]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
        ):
            result = await drv_mod.update_driver_status(
                driver_id=DRIVER_ID,
                is_online=True,
                current_user={"id": DRIVER_USER_ID},
            )

        assert result["success"] is True
        assert writes[0][1]["is_available"] is True, "pre-checks were clear — main write asserts available"
        assert len(writes) == 2, "claim landing mid-write must trigger exactly one repair write"
        repair_filters, repair_payload = writes[1]
        assert repair_payload == {"is_available": False}
        assert repair_filters == {"id": DRIVER_ID, "is_available": True}, (
            "repair must be conditional (claim_driver shape) so it can never stomp a newer state"
        )


@pytest.mark.e2e
class TestGoOnlineEligibilityRecheck:
    """Ranked audit finding #10 (docs/audit/2026-08-18-full-fleet-whole-app-audit.md):
    licence class, vehicle age, and driving experience must be re-checked on
    every go-online call, the same way document expiry already is — not just
    once at onboarding.

    Code under test: backend/routes/drivers/status.py::update_driver_status.
    """

    async def _go_online(self, driver_overrides: dict, flag_enabled: bool = True):
        from backend.routes import drivers as drv_mod

        driver = _driver_row(**driver_overrides)
        writes = []

        async def _get_rows(table, filters=None, limit=None, **kwargs):
            return []

        async def _update_one(table, filters, update):
            writes.append(update.get("$set", update))
            return {**driver, "is_online": True, "is_available": True}

        # The eligibility re-check is gated behind app_settings
        # (enforce_driver_eligibility_recheck, default off — see CLAUDE.md
        # gate #3 / the change log). Patch get_app_settings directly rather
        # than going through the "settings" table + in-process TTL cache, so
        # each test is independent of settings_loader's cache state.
        async def _fake_app_settings():
            return {"enforce_driver_eligibility_recheck": flag_enabled}

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
                AsyncMock(side_effect=[driver, {**driver, "is_online": True, "is_available": True}]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
            patch("backend.settings_loader.get_app_settings", AsyncMock(side_effect=_fake_app_settings)),
        ):
            result = await drv_mod.update_driver_status(
                driver_id=DRIVER_ID,
                is_online=True,
                current_user={"id": DRIVER_USER_ID},
            )
        return result, writes

    async def test_class_5_license_goes_online_normally(self):
        """Baseline: a standard Class 5 licence and a fresh vehicle year
        must not be blocked by the new checks."""
        result, writes = await self._go_online({})
        assert result["success"] is True
        assert writes, "driver row was not written"

    async def test_class_4_license_without_sgi_approval_is_rejected(self):
        """A Class 1-4 licence with no separate approval on file must be
        rejected with a distinct, actionable error — not lumped into a
        generic ineligibility message."""
        from backend.utils.error_handling import ErrorCode, SpinrException

        with pytest.raises(SpinrException) as excinfo:
            await self._go_online({"license_class": "4", "sgi_approved": False})
        assert excinfo.value.error_code == ErrorCode.DRIVER_LICENSE_CLASS_INELIGIBLE
        assert excinfo.value.status_code == 400

    async def test_class_4_license_with_sgi_approval_goes_online(self):
        """A non-standard class IS allowed through when the existing
        sgi_approved flag (interim proxy for 'separately approved' — no
        dedicated field exists, see change log) is set."""
        result, writes = await self._go_online({"license_class": "4", "sgi_approved": True})
        assert result["success"] is True
        assert writes

    async def test_missing_license_class_is_not_blocked(self):
        """A driver whose license_class was never backfilled (ACTION_ITEMS.md
        B14) must not be retroactively locked out by this fix — unknown data
        is left unblocked, not guessed at."""
        result, writes = await self._go_online({"license_class": None})
        assert result["success"] is True
        assert writes

    async def test_vehicle_ten_years_old_is_rejected(self):
        """Vehicle age >= 10 years must be rejected with a distinct error,
        matching the Saskatchewan Transportation Act's < 10 year rule."""
        from backend.utils.error_handling import ErrorCode, SpinrException

        old_year = datetime.now(timezone.utc).year - 10
        with pytest.raises(SpinrException) as excinfo:
            await self._go_online({"vehicle_year": old_year})
        assert excinfo.value.error_code == ErrorCode.DRIVER_VEHICLE_TOO_OLD
        assert excinfo.value.status_code == 400

    async def test_vehicle_nine_years_old_goes_online(self):
        """Just under the 10-year cutoff must not be blocked."""
        recent_year = datetime.now(timezone.utc).year - 9
        result, writes = await self._go_online({"vehicle_year": recent_year})
        assert result["success"] is True
        assert writes

    async def test_missing_vehicle_year_is_not_blocked(self):
        """No vehicle_year on file must not be guessed at / blocked."""
        result, writes = await self._go_online({"vehicle_year": None})
        assert result["success"] is True
        assert writes

    async def test_flag_disabled_does_not_block_otherwise_ineligible_driver(self):
        """Dark-ship default: with enforce_driver_eligibility_recheck OFF
        (the shipped default), even a driver who WOULD fail both new checks
        must go online unchanged — this fix must not regress any
        currently-active driver until an operator explicitly flips the flag
        on after verifying it in staging (CLAUDE.md gate #3)."""
        old_year = datetime.now(timezone.utc).year - 15
        result, writes = await self._go_online(
            {"license_class": "4", "sgi_approved": False, "vehicle_year": old_year},
            flag_enabled=False,
        )
        assert result["success"] is True
        assert writes

    async def test_driving_experience_check_not_enforced_pending_schema_field(self):
        """Documents the known, deliberate gap: no field in `drivers` or
        `driver_documents` records a licence-issue/experience-start date
        (confirmed by repo-wide search), so the 3-year-experience rule
        cannot be re-checked here without inventing a migration — which is
        out of scope for this fix per CLAUDE.md's migration conventions.
        A driver otherwise eligible must still go online; this is not a
        regression, it's the documented boundary of this change."""
        result, writes = await self._go_online({})
        assert result["success"] is True
        assert writes
