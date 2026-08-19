"""Receipt rendering — regulatory and reconciliation guarantees.

These tests pin the rules that motivate the line-item refactor:

  1. CLAUDE.md: "Rider receipts must show GST (5%) and PST (6% where
     applicable) as separate line items." — `tax_breakdown` must be
     rendered, not collapsed into a single "Tax" row, when present.

  2. Spinr is "not a hidden-fee operator" — every charge on the
     receipt maps to a disclosed line item. Area fees from
     `area_fees_breakdown` (airport, night, custom) must each appear.

  3. Surge transparency — the surge multiplier must be disclosed when
     it was in effect at booking, even though the dollar value is
     folded into distance_fare/time_fare.

  4. Decimal correctness — totals computed in the receipt must match
     the persisted `grand_total` to the cent for amounts that bite the
     int-truncation bug (e.g. 0.29, 1.13, 17.81, 77.49).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.utils.email_receipt import _receipt_total, generate_receipt_html


def _ride(**overrides) -> dict:
    """A minimal completed-ride dict with sane defaults."""
    base = {
        "id": "ride_abcdef12",
        "ride_code": "SP-12345",
        "status": "completed",
        "pickup_address": "Saskatoon, SK",
        "dropoff_address": "Regina, SK",
        "ride_completed_at": "2025-05-01T12:00:00+00:00",
        "distance_km": 4.2,
        "duration_minutes": 12,
        "base_fare": 3.50,
        "distance_fare": 6.30,
        "time_fare": 2.50,
        "booking_fee": 0.50,
        "surge_multiplier": 1.0,
        "area_fees_total": 0,
        "area_fees_breakdown": [],
        "tax_amount": 0.69,
        "tax_breakdown": {
            "GST": {"rate": 5.0, "amount": 0.64},
            "PST": {"rate": 6.0, "amount": 0.77},
        },
        "tip_amount": 0,
        "total_fare": 12.80,
        "grand_total": 14.21,
    }
    base.update(overrides)
    return base


_RIDER = {"first_name": "Avery", "last_name": "Patel", "email": "avery@example.com"}
_DRIVER = {"first_name": "Sam", "last_name": "Lee"}


# ── Tax line items ──────────────────────────────────────────────────────


class TestTaxLineItems:
    def test_gst_and_pst_appear_separately(self):
        """GST (5%) and PST (6%) must each get their own row — regulatory."""
        html = generate_receipt_html(_ride(), _RIDER, _DRIVER)
        assert "GST" in html
        assert "PST" in html
        assert "(5%)" in html  # GST rate label
        assert "(6%)" in html  # PST rate label
        # Amount values appear formatted to 2dp
        assert "$0.64" in html
        assert "$0.77" in html

    def test_hst_appears_when_present(self):
        """Combined-tax provinces (e.g. Ontario HST 13%) render a single line."""
        html = generate_receipt_html(
            _ride(
                tax_amount=1.66,
                tax_breakdown={"HST": {"rate": 13.0, "amount": 1.66}},
            ),
            _RIDER,
        )
        assert "HST" in html
        assert "(13%)" in html
        assert "$1.66" in html
        assert "GST" not in html
        assert "PST" not in html

    def test_legacy_ride_without_breakdown_falls_back_to_single_tax_row(self):
        """Legacy rides written before migration 46 have only tax_amount."""
        html = generate_receipt_html(
            _ride(tax_amount=1.50, tax_breakdown=None),
            _RIDER,
        )
        # No per-tax labels, but a generic Tax row with the total.
        assert "Tax" in html
        assert "$1.50" in html

    def test_zero_tax_renders_no_tax_rows(self):
        html = generate_receipt_html(
            _ride(tax_amount=0, tax_breakdown={}),
            _RIDER,
        )
        assert "GST" not in html
        assert "PST" not in html


# ── Area fees ───────────────────────────────────────────────────────────


class TestAreaFeeLineItems:
    def test_airport_fee_renders_as_disclosed_line_item(self):
        html = generate_receipt_html(
            _ride(
                area_fees_total=3.00,
                area_fees_breakdown=[
                    {"id": "f1", "name": "Airport pickup", "type": "airport", "calculated_value": 3.00}
                ],
                grand_total=17.21,
            ),
            _RIDER,
        )
        assert "Airport pickup" in html
        assert "$3.00" in html

    def test_multiple_area_fees_each_render(self):
        html = generate_receipt_html(
            _ride(
                area_fees_total=4.00,
                area_fees_breakdown=[
                    {"name": "Airport pickup", "calculated_value": 3.00},
                    {"name": "Night fee", "calculated_value": 1.00},
                ],
                grand_total=18.21,
            ),
            _RIDER,
        )
        assert "Airport pickup" in html
        assert "Night fee" in html

    def test_zero_value_fee_skipped(self):
        """Fees that calculated to $0.00 (e.g. night fee outside hours)
        should not pollute the receipt with a $0 row."""
        html = generate_receipt_html(
            _ride(
                area_fees_breakdown=[
                    {"name": "Phantom fee", "calculated_value": 0.00},
                ],
            ),
            _RIDER,
        )
        assert "Phantom fee" not in html


# ── Surge disclosure ────────────────────────────────────────────────────


class TestSurgeDisclosure:
    def test_surge_above_one_renders_notice(self):
        html = generate_receipt_html(_ride(surge_multiplier=1.5), _RIDER)
        assert "Surge pricing" in html
        assert "1.50" in html

    def test_surge_one_renders_no_notice(self):
        html = generate_receipt_html(_ride(surge_multiplier=1.0), _RIDER)
        assert "Surge" not in html

    def test_surge_at_cap_renders(self):
        """Auto surge cap is 2.5× — must still disclose."""
        html = generate_receipt_html(_ride(surge_multiplier=2.5), _RIDER)
        assert "Surge pricing" in html
        assert "2.50" in html


class TestSurgeDollarLineItem:
    """Ranked #26 / audit N14: surge must show as a real dollar line item, not
    just a text footnote — using the exact same Decimal formula as the
    in-app fare breakdown (routes/rides/_shared.py::_build_fare_breakdown).

    Default fixture: base=3.50, distance=6.30 (surged), time=2.50 (surged),
    booking=0.50 → components sum to 12.80 == total_fare, so surge=1.5x is
    NOT minimum-fare clamped. surged_dt=8.80, unsurged_dt=round(8.80/1.5)=5.87,
    surge_delta=round(8.80-5.87)=2.93.
    """

    def test_surge_renders_real_dollar_amount_not_just_footnote(self):
        html = generate_receipt_html(_ride(surge_multiplier=1.5), _RIDER, _DRIVER)
        assert "Surge (1.50×)" in html
        assert "$2.93" in html
        # Footnote stays alongside the dollar line as supplementary context.
        assert "Surge pricing 1.50× was in effect at booking time." in html

    def test_no_surge_line_item_when_multiplier_is_one(self):
        html = generate_receipt_html(_ride(surge_multiplier=1.0), _RIDER, _DRIVER)
        assert "Surge" not in html
        assert "$2.93" not in html

    def test_surge_line_item_zero_when_minimum_fare_absorbs_it(self):
        """A tiny surged ride floored to the minimum fare shows the Surge
        line at $0.00 (disclosure) rather than a fabricated non-zero delta —
        matches the in-app breakdown's minimum-fare-clamp behaviour."""
        ride = _ride(
            base_fare=1.00,
            distance_fare=1.00,
            time_fare=0.40,
            booking_fee=0.00,
            surge_multiplier=2.0,
            total_fare=20.00,
            grand_total=20.00,
            tax_amount=0,
            tax_breakdown={},
        )
        html = generate_receipt_html(ride, _RIDER, _DRIVER)
        assert "Surge (2.00×)" in html
        assert "$0.00" in html
        assert "Minimum fare adjustment" in html

    def test_total_still_reconciles_with_surge_line_added(self):
        """The header total (grand_total + tip) is unaffected by inserting
        the new Surge line — it's a decomposition of distance/time, not an
        additive charge."""
        ride = _ride(surge_multiplier=1.5)
        html = generate_receipt_html(ride, _RIDER, _DRIVER, tip=2.00)
        assert "$16.21" in html  # 14.21 + 2.00, unchanged by the surge split


# ── Decimal correctness ─────────────────────────────────────────────────


class TestReceiptTotal:
    def test_grand_total_includes_tip(self):
        """Persisted grand_total excludes tip; receipt total adds it back."""
        ride = _ride(grand_total=14.21, tip_amount=0)
        assert _receipt_total(ride, tip=2.00) == Decimal("16.21")

    def test_receipt_total_handles_underflow_amount(self):
        """0.29 — the canonical int(0.29*100)=28 underflow — must round-trip
        through the receipt total without losing a cent."""
        ride = _ride(grand_total=0.29, tip_amount=0)
        assert _receipt_total(ride, tip=0) == Decimal("0.29")

    @pytest.mark.parametrize(
        "fare,fees,tax,tip,expected",
        [
            (10.00, 0.00, 1.10, 0.00, "11.10"),
            (12.80, 0.00, 1.41, 2.00, "16.21"),
            (47.83, 3.00, 5.59, 5.00, "61.42"),  # SK GST+PST on $47.83 + airport
            (0.29, 0, 0, 0, "0.29"),
            (1.13, 0, 0, 0, "1.13"),
            (17.81, 0, 0, 0, "17.81"),
        ],
    )
    def test_legacy_path_sums_parts_when_grand_total_missing(self, fare, fees, tax, tip, expected):
        ride = _ride(
            total_fare=fare,
            area_fees_total=fees,
            tax_amount=tax,
            tip_amount=0,
            grand_total=None,
        )
        assert _receipt_total(ride, tip=tip) == Decimal(expected)


# ── Reconciliation: visible rows sum to header total ───────────────────


class TestReconciliation:
    def test_sk_ride_with_gst_pst_renders_total_at_header(self):
        ride = _ride()
        html = generate_receipt_html(ride, _RIDER, _DRIVER)
        # Header total = grand_total + tip = 14.21 + 0 = 14.21
        assert "$14.21" in html

    def test_total_with_tip(self):
        ride = _ride()
        html = generate_receipt_html(ride, _RIDER, _DRIVER, tip=2.00)
        # 14.21 + 2.00 = 16.21
        assert "$16.21" in html


# ── Minimum-fare uplift & airport surcharge disclosure ──────────────────


class TestMinimumFareAdjustment:
    """A minimum-fare ride charges above the sum of its components; the uplift
    (which the driver keeps) must appear as a disclosed row so the rendered
    rows reconcile to the header total."""

    def _min_ride(self, **overrides):
        # base+dist+time+booking = 5.90, floored up to 8.00 at booking.
        ride = _ride(
            base_fare=3.50,
            distance_fare=0.15,
            time_fare=0.25,
            booking_fee=2.00,
            distance_km=0.1,
            tax_amount=0,
            tax_breakdown={},
            total_fare=8.00,
            grand_total=8.00,
        )
        ride.update(overrides)
        return ride

    def test_minimum_fare_adjustment_row_rendered(self):
        html = generate_receipt_html(self._min_ride(), _RIDER, _DRIVER)
        assert "Minimum fare adjustment" in html
        assert "$2.10" in html  # 8.00 − 5.90
        assert "$8.00" in html  # header total reconciles

    def test_no_minimum_fare_row_when_not_clamped(self):
        # Default ride: total_fare 12.80 == component sum → no adjustment.
        html = generate_receipt_html(_ride(), _RIDER, _DRIVER)
        assert "Minimum fare adjustment" not in html

    def test_airport_fee_rendered_as_row(self):
        # airport_fee column sits inside total_fare but was never itemised.
        ride = _ride(airport_fee=4.00, total_fare=16.80, grand_total=18.21)
        html = generate_receipt_html(ride, _RIDER, _DRIVER)
        assert "Airport surcharge" in html
        assert "$4.00" in html
        assert "Minimum fare adjustment" not in html  # not clamped, only airport
