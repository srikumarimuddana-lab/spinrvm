"""Cancelled-ride receipts render the ACTUAL charge, not the stale quote (2026-09-23).

A cancelled ride keeps its booking-time quote in total_fare / grand_total /
tax_amount / tax_breakdown / base_fare ... (deliberately not overwritten on
cancel). Every receipt surface — JSON /receipt, the receipt PDF rider-app
downloads, and the email receipt — used to render that quote, so a rider
charged a $4.50 cancellation fee saw an $18.40 ride fare + $0.92 GST +
$19.32 total next to a separate "$4.50 cancellation fee".

See docs/change-log/2026-09-23-cancellation-fee-tax-receipt-corporate.md.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import email_receipt, receipt_pdf
from backend.utils.cancellation_receipt import cancellation_charge

pytestmark = pytest.mark.unit

RIDER_ID = "rider_rcpt"
RIDE_ID = "ride_rcpt_1"


def _cancelled(**kw) -> dict:
    base = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": None,
        "status": "cancelled",
        "cancelled_at": "2026-09-23T12:00:00+00:00",
        # Stale pre-trip quote — must appear on no receipt surface.
        "base_fare": 3.00,
        "distance_fare": 11.40,
        "time_fare": 4.00,
        "booking_fee": 0,
        "total_fare": 18.40,
        "tax_amount": 0.92,
        "tax_breakdown": {"GST": {"rate": 5.0, "amount": 0.92}},
        "grand_total": 19.32,
        "distance_km": 12.0,
        "surge_multiplier": 1.5,
        "fare_breakdown_snapshot": {"lines": [{"label": "Ride fare (12.0 km)", "amount": 18.40, "type": "ride"}]},
        # What was actually charged.
        "cancellation_fee_admin": 0.50,
        "cancellation_fee_driver": 4.00,
        "cancellation_fee_tax_amount": 0.23,
        "cancellation_fee_tax_breakdown": {"GST": {"rate": 5.0, "amount": 0.23}},
    }
    base.update(kw)
    return base


class TestCancellationCharge:
    def test_fee_plus_tax_lines_sum_to_total(self):
        c = cancellation_charge(_cancelled())
        assert c["lines"] == [
            {"label": "Cancellation fee", "amount": 4.5, "type": "fee"},
            {"label": "GST (5.0%)", "amount": 0.23, "type": "tax"},
        ]
        assert c["grand_total"] == Decimal("4.73")
        assert sum(Decimal(str(ln["amount"])) for ln in c["lines"]) == c["grand_total"]

    def test_untaxed_fee_pre_migration_or_flag_off(self):
        ride = _cancelled(cancellation_fee_tax_amount=None, cancellation_fee_tax_breakdown=None)
        c = cancellation_charge(ride)
        assert [ln["label"] for ln in c["lines"]] == ["Cancellation fee"]
        assert c["grand_total"] == Decimal("4.50")

    def test_noshow_label(self):
        c = cancellation_charge(_cancelled(cancellation_type="noshow"))
        assert c["lines"][0]["label"] == "No-show fee"

    def test_free_cancellation_is_zero_not_the_quote(self):
        ride = _cancelled(
            cancellation_fee_admin=0,
            cancellation_fee_driver=0,
            cancellation_fee_tax_amount=None,
            cancellation_fee_tax_breakdown=None,
        )
        c = cancellation_charge(ride)
        assert c["lines"] == [] and c["grand_total"] == Decimal("0.00")

    def test_scheduled_notice_fee_is_a_disclosed_line(self):
        ride = _cancelled(
            cancellation_fee_admin=None,
            cancellation_fee_driver=None,
            cancellation_fee_tax_amount=None,
            cancellation_fee_tax_breakdown=None,
            scheduled_notice_fee_amount="3.00",
        )
        c = cancellation_charge(ride)
        assert c["lines"] == [{"label": "Late cancellation fee (scheduled ride)", "amount": 3.0, "type": "fee"}]
        assert c["grand_total"] == Decimal("3.00")

    def test_tax_amount_without_breakdown_still_disclosed(self):
        c = cancellation_charge(_cancelled(cancellation_fee_tax_breakdown=None))
        assert {"label": "Tax", "amount": 0.23, "type": "tax"} in c["lines"]
        assert c["grand_total"] == Decimal("4.73")


class TestJsonReceipt:
    async def _receipt(self, ride: dict, settings: dict | None = None) -> dict:
        from backend.routes.rides import get_ride_receipt

        with (
            patch("backend.routes.rides._deps.db_supabase") as db,
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings or {})),
        ):
            db.get_ride = AsyncMock(return_value=ride)
            result = await get_ride_receipt(ride_id=RIDE_ID, current_user={"id": RIDER_ID})
        return result["receipt"]

    async def test_cancelled_receipt_renders_actual_charge(self):
        r = await self._receipt(_cancelled())
        assert r["fare_breakdown"] == cancellation_charge(_cancelled())["lines"]
        assert r["grand_total"] == 4.73
        assert r["total_charged"] == 4.73
        assert r["tax_amount"] == 0.23
        assert r["tax_breakdown"] == {"GST": {"rate": 5.0, "amount": 0.23}}
        assert r["cancellation_fee_tax"] == 0.23
        # No field carries the quote.
        for k in ("base_fare", "distance_fare", "time_fare", "booking_fee", "airport_fee", "area_fees_total"):
            assert r[k] == 0, k
        assert r["surge_multiplier"] == 1.0
        assert not any("Ride fare" in ln["label"] for ln in r["fare_breakdown"])
        # Pre-existing field keeps its pre-tax meaning.
        assert r["cancellation_fee"] == 4.5

    async def test_fare_lock_snapshot_is_ignored_for_cancelled_rides(self):
        r = await self._receipt(_cancelled(), settings={"fare_lock_enabled": True})
        assert r["fare_locked"] is False
        assert r["grand_total"] == 4.73

    async def test_completed_ride_receipt_unchanged(self):
        ride = _cancelled(status="completed", fare_breakdown_snapshot=None, surge_multiplier=1.0)
        r = await self._receipt(ride)
        assert r["grand_total"] == 19.32
        assert r["base_fare"] == 3.00
        assert "cancellation_fee_tax" not in r


class TestPdfReceipt:
    def test_cancelled_pdf_rows_are_the_fee_not_the_quote(self):
        rows, grand = receipt_pdf._fare_lines(_cancelled(), Decimal("0"))
        assert rows == [("Cancellation fee", "$4.50"), ("GST (5.0%)", "$0.23")]
        assert grand == Decimal("4.73")

    def test_free_cancellation_pdf_shows_zero(self):
        ride = _cancelled(
            cancellation_fee_admin=0,
            cancellation_fee_driver=0,
            cancellation_fee_tax_amount=None,
            cancellation_fee_tax_breakdown=None,
        )
        rows, grand = receipt_pdf._fare_lines(ride, Decimal("0"))
        assert rows == [("Cancellation fee", "$0.00")]
        assert grand == Decimal("0.00")

    def test_completed_pdf_unchanged(self):
        rows, grand = receipt_pdf._fare_lines(_cancelled(status="completed", surge_multiplier=1.0), Decimal("0"))
        assert grand == Decimal("19.32")
        assert rows[0][0] == "Base fare"

    def test_cancelled_pdf_renders(self):
        pdf = receipt_pdf.generate_receipt_pdf(_cancelled(), {"first_name": "R"})
        assert pdf.startswith(b"%PDF")


class TestEmailReceipt:
    def test_cancelled_email_rows_and_total(self):
        html, total = email_receipt._build_fare_rows(_cancelled(), Decimal("0"))
        assert total == Decimal("4.73")
        assert "Cancellation fee" in html and "GST (5.0%)" in html and "$4.73" in html
        assert "19.32" not in html and "Ride fare" not in html and "Base fare" not in html

    def test_subject_total_matches_body(self):
        assert email_receipt._receipt_total(_cancelled(), tip=0) == Decimal("4.73")

    def test_completed_email_total_unchanged(self):
        assert email_receipt._receipt_total(_cancelled(status="completed"), tip=0) == Decimal("19.32")
