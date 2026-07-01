"""
Regression coverage for card-paid cancellation fees.

Before this fix, a rider paying by card who cancelled after the free window
(or after the driver arrived) had a cancellation fee computed and recorded
on the ride (``cancellation_fee_admin`` / ``cancellation_fee_driver``), but
the card was never actually charged — only the wallet payment_method path
called out to a real charge. ``payment_status`` was left at its stale
pre-cancel value, so the rider app showed a misleading "Card · Pending" for
a charge that was never attempted.

These tests exercise ``cancel_ride_rider`` for payment_method="card" and
assert the ride's final ``payment_status``/``payment_intent_id`` reflect the
actual Stripe outcome from ``charge_ancillary_fee``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

RIDER_ID = "rider_card_cancel"
DRIVER_USER_ID = "driver_user_card_cancel"
DRIVER_ID = "driver_card_cancel"
RIDE_ID = "ride_card_cancel_001"


def _ride(status: str, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "payment_method": "card",
        "payment_method_id": "pm_test_123",
        "driver_accepted_at": None,
    }
    row.update(extra)
    return row


def _driver() -> dict:
    return {"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "name": "Test Driver"}


def _rider_user() -> dict:
    return {"id": RIDER_ID, "stripe_customer_id": "cus_test_123"}


SETTINGS = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 4.00}


@pytest.mark.e2e
@pytest.mark.asyncio
class TestCardCancellationFeeCharge:
    async def test_succeeded_charge_marks_ride_paid(self):
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(status="driver_arrived")
        ride_cancelled = _ride(status="cancelled")
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride_arrived)),
            patch("backend.routes.rides.get_app_settings", AsyncMock(return_value=SETTINGS)),
            patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value=_rider_user())),
            patch(
                "backend.routes.rides.charge_ancillary_fee",
                AsyncMock(
                    return_value=ChargeOutcome(
                        status="succeeded", payment_intent_id="pi_test_ok", charged_amount=Decimal("4.50")
                    )
                ),
            ) as charge_mock,
            patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.rides.db.update_one", AsyncMock()),
            patch("backend.routes.rides.db.insert_one", AsyncMock()) as insert_one_mock,
            patch("backend.routes.rides.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                reason="",
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert result["cancellation_fee"] == Decimal("4.50")
        charge_mock.assert_awaited_once()
        _, charge_kwargs = charge_mock.call_args
        assert charge_kwargs["amount"] == Decimal("4.50")
        assert charge_kwargs["payment_method_id"] == "pm_test_123"
        assert charge_kwargs["stripe_customer_id"] == "cus_test_123"
        assert charge_kwargs["fee_type"] == "cancellation_fee"

        # The ride row must be updated with the real charge outcome, not left
        # at a stale pre-cancel payment_status.
        written = update_ride_mock.call_args_list[0].args[1]
        assert written["payment_status"] == "paid"
        assert written["payment_intent_id"] == "pi_test_ok"

        # A successful Stripe charge must leave a reconciliation trail — a
        # financial_events row written BEFORE the ride update, mirroring
        # settle_card's record_payment_event, so a crash between the two
        # never loses track of money already collected.
        ledger_calls = [c for c in insert_one_mock.call_args_list if c.args[0] == "financial_events"]
        assert len(ledger_calls) == 1
        ledger_row = ledger_calls[0].args[1]
        assert ledger_row["ride_id"] == RIDE_ID
        assert ledger_row["ref"] == "pi_test_ok"
        assert ledger_row["delta_cents"] == 450

    async def test_declined_charge_marks_ride_failed(self):
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(status="driver_arrived")
        ride_cancelled = _ride(status="cancelled")
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride_arrived)),
            patch("backend.routes.rides.get_app_settings", AsyncMock(return_value=SETTINGS)),
            patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value=_rider_user())),
            patch(
                "backend.routes.rides.charge_ancillary_fee",
                AsyncMock(
                    return_value=ChargeOutcome(
                        status="declined", decline_code="insufficient_funds", error_message="Card declined"
                    )
                ),
            ),
            patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.rides.db.update_one", AsyncMock()),
            patch("backend.routes.rides.db.insert_one", AsyncMock()),
            patch("backend.routes.rides.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                reason="",
                current_user={"id": RIDER_ID},
            )

        # Cancel still succeeds — a declined fee never blocks the cancel itself,
        # it's a collections problem, not a reason to strand the ride/driver.
        assert result["success"] is True
        written = update_ride_mock.call_args_list[0].args[1]
        assert written["payment_status"] == "failed"
        # A decline never returns a PaymentIntent id — payment_intent_id must
        # still be explicitly overwritten (here, to None) rather than left at
        # whatever stale booking-time hold PI the ride already had. Otherwise
        # payment_retry.py's blind payment_status scan would pick up this
        # cancelled ride and retry an unrelated PaymentIntent forever.
        assert "payment_intent_id" in written
        assert written["payment_intent_id"] is None

    async def test_requires_action_also_marks_ride_failed(self):
        """No 3DS retry flow exists for a cancellation fee, so requires_action
        is treated the same as a decline — but must still overwrite
        payment_intent_id to the real (challenged) PI, not leave it unset."""
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(status="driver_arrived")
        ride_cancelled = _ride(status="cancelled")
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride_arrived)),
            patch("backend.routes.rides.get_app_settings", AsyncMock(return_value=SETTINGS)),
            patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value=_rider_user())),
            patch(
                "backend.routes.rides.charge_ancillary_fee",
                AsyncMock(
                    return_value=ChargeOutcome(
                        status="requires_action", payment_intent_id="pi_test_3ds", client_secret="secret_x"
                    )
                ),
            ),
            patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.rides.db.update_one", AsyncMock()),
            patch("backend.routes.rides.db.insert_one", AsyncMock()),
            patch("backend.routes.rides.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                reason="",
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        written = update_ride_mock.call_args_list[0].args[1]
        assert written["payment_status"] == "failed"
        assert written["payment_intent_id"] == "pi_test_3ds"

    async def test_unconfigured_stripe_leaves_payment_status_untouched(self):
        """When Stripe isn't wired up at all, no charge was attempted — the
        ride's existing payment_status/payment_intent_id must be left alone
        rather than mislabelled as a decline."""
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(status="driver_arrived")
        ride_cancelled = _ride(status="cancelled")
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride_arrived)),
            patch("backend.routes.rides.get_app_settings", AsyncMock(return_value=SETTINGS)),
            patch("backend.routes.rides.db_supabase.get_user_by_id", AsyncMock(return_value=_rider_user())),
            patch(
                "backend.routes.rides.charge_ancillary_fee",
                AsyncMock(return_value=ChargeOutcome(status="unconfigured", error_message="not configured")),
            ),
            patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.rides.db.update_one", AsyncMock()),
            patch("backend.routes.rides.db.insert_one", AsyncMock()),
            patch("backend.routes.rides.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                reason="",
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        written = update_ride_mock.call_args_list[0].args[1]
        assert "payment_status" not in written
        assert "payment_intent_id" not in written

    async def test_company_allowance_never_calls_card_charge(self):
        """Corporate-paid rides must not attempt a Stripe charge on the rider's
        personal card — that billing rail isn't wired up here (would charge
        the wrong party)."""
        from backend.routes import rides as rides_mod

        ride_arrived = _ride(status="driver_arrived", payment_method="company_allowance")
        ride_cancelled = _ride(status="cancelled", payment_method="company_allowance")
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides.db.find_one", AsyncMock(return_value=ride_arrived)),
            patch("backend.routes.rides.get_app_settings", AsyncMock(return_value=SETTINGS)),
            patch("backend.routes.rides.charge_ancillary_fee", AsyncMock()) as charge_mock,
            patch("backend.routes.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.rides.db.update_one", AsyncMock()),
            patch("backend.routes.rides.db.insert_one", AsyncMock()),
            patch("backend.routes.rides.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=ride_cancelled)),
            patch("backend.routes.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.rides.send_push_notification", AsyncMock()),
        ):
            fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
            result = await fn(
                request=MagicMock(),
                ride_id=RIDE_ID,
                reason="",
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        charge_mock.assert_not_awaited()
        written = update_ride_mock.call_args_list[0].args[1]
        assert "payment_status" not in written
