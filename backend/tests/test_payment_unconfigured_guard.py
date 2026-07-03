"""Production guard for the Stripe-unconfigured settlement paths.

charge_ride/capture_ride return status "unconfigured" when stripe_secret_key
is empty. In dev/test the settlement paths mark the ride paid so local flows
don't wedge — but the key lives in the app_settings DB row (not env), so
there is NO startup fail-fast. If the row were ever blanked in production
(e.g. mid key-rotation), every settling ride would silently be marked paid
without a charge. settle_card must refuse loudly instead: 503, ride left
collectible (payment_status=failed), never paid.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

RIDE_ID = "ride_uncfg_1"
RIDER_ID = "rider_uncfg_1"


def _ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "completed",
        "payment_method": "card",
        "payment_status": "processing",
        "total_fare": 18.5,
        "tip_amount": 0,
    }
    row.update(extra)
    return row


def _rider_user() -> dict:
    return {
        "id": RIDER_ID,
        "stripe_customer_id": "cus_uncfg",
        "default_payment_method": "pm_uncfg",
    }


def _patches(updates: list, *, outcome: ChargeOutcome, env: str):
    return [
        patch("backend.services.payment_service.app_config", MagicMock(ENV=env)),
        patch(
            "backend.services.payment_service.db_supabase.get_user_by_id",
            AsyncMock(return_value=_rider_user()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.update_ride",
            AsyncMock(side_effect=lambda _rid, body: updates.append(body) or {"id": RIDE_ID}),
        ),
        patch("backend.services.payment_service.charge_ride", AsyncMock(return_value=outcome)),
        patch("backend.services.payment_service.capture_ride", AsyncMock(return_value=outcome)),
    ]


def _last_payment_status(updates: list) -> str | None:
    for body in reversed(updates):
        if "payment_status" in body:
            return body["payment_status"]
    return None


@pytest.mark.asyncio
class TestUnconfiguredProductionGuard:
    async def test_fresh_charge_unconfigured_refused_in_production(self):
        from backend.services import payment_service

        updates: list = []
        outcome = ChargeOutcome(status="unconfigured", error_message="no key")

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in _patches(updates, outcome=outcome, env="production"):
                st.enter_context(p)
            result = await payment_service.settle_card(
                ride=_ride(),
                ride_id=RIDE_ID,
                rider_id=RIDER_ID,
                total_charge=Decimal("18.50"),
                tip_amount=Decimal("0"),
            )

        assert result.success is False
        assert result.status_code == 503
        assert result.error_code == "stripe_unconfigured"
        # Ride must remain collectible — failed, never paid.
        assert _last_payment_status(updates) == "failed"
        assert all(u.get("payment_status") != "paid" for u in updates)

    async def test_held_capture_unconfigured_refused_in_production(self):
        from backend.services import payment_service

        updates: list = []
        outcome = ChargeOutcome(status="unconfigured", error_message="no key")

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in _patches(updates, outcome=outcome, env="production"):
                st.enter_context(p)
            result = await payment_service.settle_card(
                ride=_ride(
                    payment_intent_id="pi_hold_1",
                    auth_status="authorized",
                    authorized_amount=25.00,
                ),
                ride_id=RIDE_ID,
                rider_id=RIDER_ID,
                total_charge=Decimal("18.50"),
                tip_amount=Decimal("0"),
            )

        assert result.success is False
        assert result.status_code == 503
        assert result.error_code == "stripe_unconfigured"
        assert _last_payment_status(updates) == "failed"
        assert all(u.get("payment_status") != "paid" for u in updates)

    async def test_dev_env_still_marks_paid(self):
        """The dev/test fallback is preserved: unconfigured marks paid so
        local flows without Stripe wiring don't wedge."""
        from backend.services import payment_service

        updates: list = []
        outcome = ChargeOutcome(status="unconfigured", error_message="no key")

        from contextlib import ExitStack

        with ExitStack() as st:
            for p in _patches(updates, outcome=outcome, env="test"):
                st.enter_context(p)
            result = await payment_service.settle_card(
                ride=_ride(),
                ride_id=RIDE_ID,
                rider_id=RIDER_ID,
                total_charge=Decimal("18.50"),
                tip_amount=Decimal("0"),
            )

        assert result.success is True
        assert _last_payment_status(updates) == "paid"
