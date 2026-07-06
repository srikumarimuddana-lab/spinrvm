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


def test_no_surge_line_when_multiplier_is_one():
    fb = calculate_fare(_FARE_INFO, distance_km=10.0, duration_minutes=15.0, surge=Decimal("1"))
    lines = build_fare_breakdown_lines(fb, distance_km=10.0, duration_minutes=15)
    assert not [x for x in lines if x["type"] == "modifier" and x["label"].startswith("Surge")]
    # Ride line still equals base + distance + time when unsurged.
    ride = next(x for x in lines if x["type"] == "ride")
    assert abs(float(ride["amount"]) - float(fb.base_fare + fb.distance_fare + fb.time_fare)) < 0.011
