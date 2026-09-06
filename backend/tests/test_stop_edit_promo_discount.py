"""A mid-trip stop edit must not silently drop the promo discount (audit N3).

`_reestimate_fare_for_stops` computed:

    grand_total = _round(new_total + fees_total + tax_amount)

omitting `- discount`, unlike the settlement recompute in
`services/fare_service.py:468`. Stop edits are allowed in `in_progress`
(`routes/rides/stops.py`), and settlement charges the stored `grand_total`
(`routes/rides/payments.py`), so adding a stop to a promo ride re-added the
discount to the rider's charge while `_ride_receipt_lines` still rendered the
`-$X` promo line — the charge stopped matching the disclosed line items.

Worked example from the audit: a $20 fare with a $5 promo books at $15.75.
Adding a $3 stop charged $24.15 instead of $19.15 — a $5.00 overcharge.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


def _ride(**kw):
    """A $20 ride: base $3, distance $12, time $4, booking fee $1."""
    base = {
        "id": "ride-promo-1",
        "pickup_lat": 52.10,
        "pickup_lng": -106.60,
        "dropoff_lat": 52.20,
        "dropoff_lng": -106.70,
        "distance_km": 10.0,
        "duration_minutes": 20,
        "surge_multiplier": 1.0,
        "base_fare": 3.0,
        "distance_fare": 12.0,
        "time_fare": 4.0,
        "booking_fee": 1.0,
        "airport_fee": 0,
        "discount_amount": "5.00",
        "promo_code": "SAVE5",
    }
    base.update(kw)
    return base


async def _reestimate(ride, stops, *, fees_total="0", tax_amount="0"):
    from backend.routes.rides._shared import _reestimate_fare_for_stops

    with patch(
        "backend.routes.rides._deps.calculate_all_fees",
        AsyncMock(
            return_value={
                "fees_total": fees_total,
                "tax_amount": tax_amount,
                "tax_breakdown": {},
                "fees": [],
            }
        ),
    ):
        return await _reestimate_fare_for_stops(ride, stops)


class TestStopEditKeepsPromoDiscount:
    async def test_discount_is_subtracted_from_grand_total(self):
        result = await _reestimate(_ride(), [])
        total = Decimal(result["total_fare"])
        grand = Decimal(result["grand_total"])
        assert grand == total - Decimal("5.00")

    async def test_adding_a_stop_does_not_re_add_the_discount(self):
        """The regression itself: the delta between the no-stop and with-stop
        grand totals must be the added fare only, never the fare plus the
        silently-restored promo."""
        ride = _ride()
        before = Decimal((await _reestimate(ride, []))["grand_total"])
        after = Decimal((await _reestimate(ride, [{"lat": 52.15, "lng": -106.65}]))["grand_total"])
        added_fare = Decimal((await _reestimate(ride, [{"lat": 52.15, "lng": -106.65}]))["total_fare"]) - Decimal(
            (await _reestimate(ride, []))["total_fare"]
        )
        assert after - before == added_fare

    async def test_grand_total_matches_the_rendered_receipt_math(self):
        """CLAUDE.md: every charge maps to a disclosed line item. The charged
        grand_total must equal fare + fees + tax - the promo line the receipt
        renders."""
        result = await _reestimate(_ride(), [{"lat": 52.15, "lng": -106.65}], fees_total="2.00", tax_amount="1.15")
        expected = Decimal(result["total_fare"]) + Decimal("2.00") + Decimal("1.15") - Decimal("5.00")
        assert Decimal(result["grand_total"]) == expected

    async def test_no_discount_ride_is_unchanged(self):
        """Guard against over-correcting: rides with no promo must compute
        exactly as before."""
        result = await _reestimate(
            _ride(discount_amount=None, promo_code=None),
            [{"lat": 52.15, "lng": -106.65}],
            fees_total="2.00",
            tax_amount="1.15",
        )
        expected = Decimal(result["total_fare"]) + Decimal("2.00") + Decimal("1.15")
        assert Decimal(result["grand_total"]) == expected

    async def test_grand_total_never_goes_negative(self):
        """Stops can be *removed* (DELETE /{ride_id}/stops/{index}), shrinking
        the fare. A promo larger than what is left must floor at zero, not
        produce a negative charge."""
        tiny = _ride(
            base_fare=0.5,
            distance_fare=0.5,
            time_fare=0.5,
            booking_fee=0.0,
            discount_amount="50.00",
            dropoff_lat=52.1001,
            dropoff_lng=-106.6001,
        )
        result = await _reestimate(tiny, [])
        assert Decimal(result["grand_total"]) == Decimal("0")
