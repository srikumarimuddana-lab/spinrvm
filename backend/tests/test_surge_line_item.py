"""C4: surge must be itemised as a real dollar amount, not amount=None, and the
line-item total must be unchanged by the split."""

from decimal import Decimal

from backend.services.fare_service import build_fare_breakdown_lines, calculate_fare

_FARE_INFO = {
    "base_fare": 3.0,
    "per_km_rate": 1.5,
    "per_minute_rate": 0.3,
    "booking_fee": 1.0,
    "minimum_fare": 5.0,
}


def _lines_total(lines):
    return sum(Decimal(str(line["amount"])) for line in lines if line.get("amount") is not None)


def test_surge_line_carries_real_dollar_amount():
    fb = calculate_fare(_FARE_INFO, distance_km=10.0, duration_minutes=15.0, surge=Decimal("1.5"))
    lines = build_fare_breakdown_lines(fb, distance_km=10.0, duration_minutes=15)

    surge_lines = [x for x in lines if x["type"] == "modifier" and x["label"].startswith("Surge")]
    assert surge_lines, "expected a Surge line when surge > 1"
    amount = surge_lines[0]["amount"]
    assert amount is not None, "C4: surge line must show a dollar amount, not None"

    # Surge delta = surged(distance+time) - unsurged(distance+time).
    expected = float((fb.distance_fare + fb.time_fare) - (fb.distance_fare_base + fb.time_fare_base))
    assert abs(float(amount) - expected) < 0.011

    # The split preserves the grand total: base(pre-surge ride) + surge + booking.
    total = _lines_total(lines)
    assert abs(float(total) - float(fb.base_fare + fb.distance_fare + fb.time_fare + fb.booking_fee)) < 0.011


def test_surge_not_overstated_on_minimum_fare_ride():
    """Codex-3: a short ride whose surged fare is still under the minimum must
    NOT show the full raw surge delta — surge added only what pushed the bill
    above the no-surge total (here: nothing)."""
    tiny = {
        "base_fare": 1.0,
        "per_km_rate": 0.5,
        "per_minute_rate": 0.1,
        "booking_fee": 0.0,
        "minimum_fare": 20.0,  # floor well above even the surged fare
    }
    fb = calculate_fare(tiny, distance_km=1.0, duration_minutes=2.0, surge=Decimal("2.0"))
    # Surged subtotal (1 + 1.0 + 0.4 = 2.4) is far below the 20.0 minimum, so
    # surge is fully absorbed by the floor.
    assert float(fb.total_fare) == 20.0
    lines = build_fare_breakdown_lines(fb, distance_km=1.0, duration_minutes=2)
    surge_lines = [x for x in lines if x["type"] == "modifier" and x["label"].startswith("Surge")]
    assert surge_lines and float(surge_lines[0]["amount"]) == 0.0, "surge under the floor added $0"
    # Lines still sum to the (minimum) total.
    assert abs(float(_lines_total(lines)) - 20.0) < 0.011


def test_min_fare_uplift_attributed_and_receipt_truthful():
    """The bug this fixes: on a minimum-fare ride the receipt's 'Ride fare' line
    (disclosed as 'Driver earns 100%') must equal the stored driver_earnings, and
    driver_earnings + admin_earnings must equal the charged total_fare. Before the
    fix the driver line was booked from the un-clamped subtotal, so the receipt
    claimed driver income the payout ledger never recorded."""
    floored = {
        "base_fare": 3.0,
        "per_km_rate": 1.5,
        "per_minute_rate": 0.3,
        "booking_fee": 1.0,
        "minimum_fare": 20.0,  # floor above the natural fare
    }
    fb = calculate_fare(floored, distance_km=1.0, duration_minutes=2.0)
    assert float(fb.total_fare) == 20.0  # clamped up to the floor

    # Ledger reconciles: charged == driver + admin (admin = booking fee here).
    assert fb.driver_earnings + fb.admin_earnings == fb.total_fare
    assert fb.admin_earnings == Decimal("1.00")
    assert fb.driver_earnings == Decimal("19.00")

    # Receipt ride line (driver's 100% share) == the stored driver_earnings.
    lines = build_fare_breakdown_lines(fb, distance_km=1.0, duration_minutes=2)
    ride_line = next(x for x in lines if x["type"] == "ride")
    assert ride_line["amount"] == float(fb.driver_earnings)
    # And all lines still sum to the charged total.
    assert abs(float(_lines_total(lines)) - float(fb.total_fare)) < 0.011


def test_no_surge_line_when_multiplier_is_one():
    fb = calculate_fare(_FARE_INFO, distance_km=10.0, duration_minutes=15.0, surge=Decimal("1"))
    lines = build_fare_breakdown_lines(fb, distance_km=10.0, duration_minutes=15)
    assert not [x for x in lines if x["type"] == "modifier" and x["label"].startswith("Surge")]
    # Ride line still equals base + distance + time when unsurged.
    ride = next(x for x in lines if x["type"] == "ride")
    assert abs(float(ride["amount"]) - float(fb.base_fare + fb.distance_fare + fb.time_fare)) < 0.011
