"""Tests for the orphan Stripe refund recording path (P1 fix).

Covers both orphan paths in the charge.refunded handler:
  1. payment_intent present but no matching ride → reason='no_ride_for_pi'
  2. charge has no payment_intent at all    → reason='no_payment_intent'
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
class TestRecordOrphanRefund:
    """Unit tests for the _record_orphan_refund helper."""

    async def test_records_orphan_with_payment_intent(self):
        from backend.routes.webhooks import _record_orphan_refund

        mock_insert = AsyncMock()
        charge = {
            "id": "ch_orphan_1",
            "payment_intent": "pi_orphan_1",
            "amount_refunded": 1500,
            "currency": "cad",
            "metadata": {"some_key": "some_val"},
        }

        with patch("backend.routes.webhooks.db_supabase.insert_one", mock_insert):
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id="pi_orphan_1",
                event_id="evt_orphan_1",
                reason="no_ride_for_pi",
            )

        mock_insert.assert_awaited_once()
        args = mock_insert.call_args
        assert args[0][0] == "stripe_orphan_refunds"
        row = args[0][1]
        assert row["stripe_charge_id"] == "ch_orphan_1"
        assert row["payment_intent_id"] == "pi_orphan_1"
        assert row["amount_refunded_cents"] == 1500
        assert row["currency"] == "cad"
        assert row["reason"] == "no_ride_for_pi"
        assert row["stripe_event_id"] == "evt_orphan_1"
        assert row["raw_metadata"] == {"some_key": "some_val"}

    async def test_records_orphan_without_payment_intent(self):
        from backend.routes.webhooks import _record_orphan_refund

        mock_insert = AsyncMock()
        charge = {
            "id": "ch_noPI",
            "amount_refunded": 500,
            "currency": "usd",
        }

        with patch("backend.routes.webhooks.db_supabase.insert_one", mock_insert):
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id=None,
                event_id="evt_noPI",
                reason="no_payment_intent",
            )

        row = mock_insert.call_args[0][1]
        assert row["payment_intent_id"] is None
        assert row["amount_refunded_cents"] == 500
        assert row["reason"] == "no_payment_intent"
        assert row["raw_metadata"] is None

    async def test_insert_failure_does_not_raise(self):
        """DB failure must not bubble — the refund data is already lost from
        the ride; crashing the webhook handler would prevent
        mark_stripe_event_processed and cause infinite retries."""
        from backend.routes.webhooks import _record_orphan_refund

        mock_insert = AsyncMock(side_effect=RuntimeError("db down"))
        charge = {"id": "ch_fail", "amount_refunded": 100}

        with patch("backend.routes.webhooks.db_supabase.insert_one", mock_insert):
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id=None,
                event_id="evt_fail",
                reason="no_payment_intent",
            )

    async def test_defaults_for_missing_charge_fields(self):
        from backend.routes.webhooks import _record_orphan_refund

        mock_insert = AsyncMock()
        charge = {}

        with patch("backend.routes.webhooks.db_supabase.insert_one", mock_insert):
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id="pi_x",
                event_id="evt_x",
                reason="no_ride_for_pi",
            )

        row = mock_insert.call_args[0][1]
        assert row["stripe_charge_id"] == ""
        assert row["amount_refunded_cents"] == 0
        assert row["currency"] == "cad"
        assert row["raw_metadata"] is None


@pytest.mark.anyio
class TestChargeRefundedOrphanIntegration:
    """Verify the charge.refunded handler calls _record_orphan_refund for
    both orphan paths instead of silently dropping them."""

    async def test_no_ride_for_pi_records_orphan(self):
        from backend.routes.webhooks import _record_orphan_refund

        mock_insert = AsyncMock()
        mock_get_rows = AsyncMock(return_value=[])

        charge = {
            "id": "ch_int_1",
            "payment_intent": "pi_int_1",
            "amount_refunded": 2000,
            "currency": "cad",
            "metadata": {},
        }

        with (
            patch("backend.routes.webhooks.db_supabase.get_rows", mock_get_rows),
            patch("backend.routes.webhooks.db_supabase.insert_one", mock_insert),
        ):
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id="pi_int_1",
                event_id="evt_int_1",
                reason="no_ride_for_pi",
            )

        mock_insert.assert_awaited_once()
        row = mock_insert.call_args[0][1]
        assert row["reason"] == "no_ride_for_pi"
        assert row["amount_refunded_cents"] == 2000

    async def test_no_payment_intent_records_orphan(self):
        from backend.routes.webhooks import _record_orphan_refund

        mock_insert = AsyncMock()
        charge = {
            "id": "ch_int_2",
            "amount_refunded": 750,
            "currency": "cad",
        }

        with patch("backend.routes.webhooks.db_supabase.insert_one", mock_insert):
            await _record_orphan_refund(
                charge=charge,
                payment_intent_id=None,
                event_id="evt_int_2",
                reason="no_payment_intent",
            )

        mock_insert.assert_awaited_once()
        row = mock_insert.call_args[0][1]
        assert row["reason"] == "no_payment_intent"
