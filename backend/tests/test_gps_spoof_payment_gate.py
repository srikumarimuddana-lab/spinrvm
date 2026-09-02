"""Pre-charge GPS-spoof gate (item #4 of the 2026-09-02 GPS-to-billing audit).

Scenarios:
  1. Flag off (default)                -> charge proceeds untouched (fail open).
  2. Flag on, verdict not landed yet    -> charge proceeds untouched (fail open).
  3. Flag on, verdict clean/below cap   -> charge proceeds untouched.
  4. Flag on, verdict likely_spoofed
     past the threshold                -> held, no charge, held_for_review response.
  5. Ride already held_for_review       -> repeats the held response, no re-evaluation.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

RIDER_ID = "rider_spoof_gate"
RIDE_ID = "ride_spoof_gate_001"


def _completed_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "completed",
        "payment_status": "pending",
        "total_fare": 18.5,
        "tip_amount": 0,
        "payment_method": "card",
    }
    row.update(extra)
    return row


def _payment_request(tip: float = 0.0):
    req = MagicMock()
    req.tip_amount = Decimal(str(tip))
    return req


def _route_rows(gps_route_validation: dict | None):
    if gps_route_validation is None:
        return []
    return [{"route_quality": {"gps_route_validation": gps_route_validation}}]


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSpoofGateFailsOpen:
    """The gate must never add friction to the normal (non-spoofed) charge path."""

    async def test_flag_off_reaches_the_atomic_claim_unheld(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.payments.get_app_settings", AsyncMock(return_value={})),
            # Force a distinguishable failure at the atomic-claim step so we can
            # prove the gate did NOT short-circuit before it.
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
                )
        assert exc.value.status_code == 409
        assert "processing" in exc.value.detail

    async def test_flag_on_but_verdict_not_landed_yet_reaches_the_atomic_claim_unheld(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        get_rows = AsyncMock(return_value=_route_rows(None))
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides.payments.get_app_settings",
                AsyncMock(return_value={"gps_spoof_charge_gate_enabled": True}),
            ),
            patch("backend.routes.rides._deps.db_supabase.get_rows", get_rows),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
                )
        assert exc.value.status_code == 409
        get_rows.assert_awaited_once()

    async def test_flag_on_verdict_clean_reaches_the_atomic_claim_unheld(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        clean_verdict = {"verdict": "clean", "deviation_pct": 2.0}
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides.payments.get_app_settings",
                AsyncMock(return_value={"gps_spoof_charge_gate_enabled": True}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=_route_rows(clean_verdict)),
            ),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
                )
        assert exc.value.status_code == 409

    async def test_flag_on_spoofed_but_below_threshold_reaches_the_atomic_claim_unheld(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        # verdict is likely_spoofed but deviation is under the (default 40%) cap.
        borderline_verdict = {"verdict": "likely_spoofed", "deviation_pct": 15.0}
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides.payments.get_app_settings",
                AsyncMock(return_value={"gps_spoof_charge_gate_enabled": True}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=_route_rows(borderline_verdict)),
            ),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.process_payment(
                    ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
                )
        assert exc.value.status_code == 409


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSpoofGateHolds:
    async def test_flag_on_verdict_spoofed_past_threshold_holds_without_charging(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        spoofed_verdict = {"verdict": "likely_spoofed", "deviation_pct": 62.5}
        update_one = AsyncMock(return_value={"id": RIDE_ID})
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides.payments.get_app_settings",
                AsyncMock(return_value={"gps_spoof_charge_gate_enabled": True}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=_route_rows(spoofed_verdict)),
            ),
            patch("backend.routes.rides._deps.db_supabase.update_one", update_one),
        ):
            result = await rides_mod.process_payment(
                ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
            )

        assert result["success"] is False
        assert result["held_for_review"] is True
        update_one.assert_awaited_once_with(
            "rides",
            {"id": RIDE_ID, "payment_status": "pending"},
            {"payment_status": "held_for_review"},
        )

    async def test_custom_threshold_from_app_settings_is_honored(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride()
        # 25% deviation would NOT trip the default 40% cap, but does trip a
        # tighter admin-configured 20% cap.
        verdict = {"verdict": "likely_spoofed", "deviation_pct": 25.0}
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides.payments.get_app_settings",
                AsyncMock(
                    return_value={
                        "gps_spoof_charge_gate_enabled": True,
                        "gps_spoof_deviation_hold_threshold_pct": 20.0,
                    }
                ),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=_route_rows(verdict)),
            ),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        ):
            result = await rides_mod.process_payment(
                ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
            )

        assert result["held_for_review"] is True

    async def test_already_held_ride_repeats_the_held_response_without_reevaluating(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride(payment_status="held_for_review")
        get_app_settings = AsyncMock()
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides.payments.get_app_settings", get_app_settings),
        ):
            result = await rides_mod.process_payment(
                ride_id=RIDE_ID, req=_payment_request(), current_user={"id": RIDER_ID}
            )

        assert result["success"] is False
        assert result["held_for_review"] is True
        get_app_settings.assert_not_awaited()
