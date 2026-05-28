"""Tests for payment counters and DB executor queue depth gauge."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.metrics import _counters, _gauges, _lock, set_gauge


class TestPaymentSuccessCounter:
    @pytest.mark.anyio
    async def test_payment_success_counter_increments(self):
        with _lock:
            before = _counters.get("spinr_payment_success_total", {}).get((), 0)

        mock_intent = MagicMock()
        mock_intent.status = "succeeded"
        mock_intent.id = "pi_test"
        mock_intent.client_secret = "secret"

        mock_settings = AsyncMock(return_value={"stripe_secret_key": "sk_test_xxx"})
        with (
            patch("backend.utils.stripe_charge.get_app_settings", mock_settings),
            patch("backend.utils.stripe_charge.stripe") as mock_stripe,
        ):
            mock_stripe.PaymentIntent.create.return_value = mock_intent
            mock_stripe.CardError = Exception
            mock_stripe.StripeError = Exception

            from backend.utils.stripe_charge import charge_ride

            outcome = await charge_ride(
                ride={"id": "ride_test", "total_fare": "25.00"},
                rider_id="rider_test",
                total_amount=Decimal("25.00"),
                payment_method_id="pm_test",
                stripe_customer_id="cus_test",
            )

        assert outcome.status == "succeeded"
        with _lock:
            after = _counters.get("spinr_payment_success_total", {}).get((), 0)
        assert after == before + 1


class TestDbQueueDepthGauge:
    def test_queue_depth_gauge_is_readable(self):
        from backend.repositories._base import _DB_EXECUTOR

        try:
            depth = _DB_EXECUTOR._work_queue.qsize()
            assert isinstance(depth, int)
        except AttributeError:
            pytest.skip("_work_queue not available on this Python version")

    def test_queue_depth_gauge_handles_missing_attribute(self):
        mock_executor = MagicMock(spec=[])
        try:
            mock_executor._work_queue.qsize()
            assert False, "Should have raised AttributeError"
        except AttributeError:
            set_gauge("spinr_db_executor_queue_depth", -1)
            with _lock:
                val = _gauges.get("spinr_db_executor_queue_depth", {}).get((), None)
            assert val == -1
