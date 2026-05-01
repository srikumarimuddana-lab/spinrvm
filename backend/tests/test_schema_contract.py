"""Phase 5 contract test — money fields serialize as Decimal strings.

Regression guard for the Phase 1 wire-format fix (PR #324). Any future
contributor who removes `class Config: json_encoders = {Decimal: str}`
from a money-bearing Pydantic model will trip this test.
"""

import json
from decimal import Decimal

import pytest

try:
    from backend.schemas import AppSettings, FareConfig, Ride, RideRatingRequest, ServiceArea
except ImportError:
    from schemas import AppSettings, FareConfig, Ride, RideRatingRequest, ServiceArea


def _model_to_json_dict(model) -> dict:
    """Round-trip a Pydantic v1 model through .json() to mirror what FastAPI
    sends on the wire (jsonable_encoder honours model Config json_encoders)."""
    return json.loads(model.json())


@pytest.mark.unit
class TestMoneyFieldsSerializeAsString:
    """Every money field on every response model must serialize as `"X.XX"`,
    never as a JS-float `X.X`. Float on the wire is the bug Phase 1 fixed."""

    def test_app_settings_money_is_string(self):
        m = AppSettings()  # uses defaults
        d = _model_to_json_dict(m)
        for f in ("cancellation_fee_admin", "cancellation_fee_driver", "platform_fee_percent"):
            assert isinstance(d[f], str), f"{f!r} should be str, got {type(d[f]).__name__}: {d[f]!r}"

    def test_service_area_airport_fee_is_string(self):
        m = ServiceArea(name="t", city="t", polygon=[{"lat": 0.0, "lng": 0.0}])
        d = _model_to_json_dict(m)
        assert isinstance(d["airport_fee"], str)
        # surge_multiplier is intentionally float — it's a multiplier, not money
        assert isinstance(d["surge_multiplier"], float)

    def test_fare_config_money_is_string(self):
        m = FareConfig(
            service_area_id="a",
            vehicle_type_id="b",
            base_fare=Decimal("3.00"),
            per_km_rate=Decimal("1.50"),
            per_minute_rate=Decimal("0.25"),
            minimum_fare=Decimal("5.00"),
        )
        d = _model_to_json_dict(m)
        for f in ("base_fare", "per_km_rate", "per_minute_rate", "minimum_fare", "booking_fee"):
            assert isinstance(d[f], str), f"{f!r} should be str, got {type(d[f]).__name__}"

    def test_ride_money_fields_are_strings(self):
        m = Ride(
            rider_id="r",
            vehicle_type_id="v",
            pickup_address="a",
            pickup_lat=0.0,
            pickup_lng=0.0,
            dropoff_address="b",
            dropoff_lat=0.0,
            dropoff_lng=0.0,
            distance_km=1.0,
            duration_minutes=1,
            base_fare=Decimal("3.00"),
            total_fare=Decimal("10.00"),
        )
        d = _model_to_json_dict(m)
        for f in (
            "base_fare",
            "distance_fare",
            "time_fare",
            "booking_fee",
            "total_fare",
            "tip_amount",
            "driver_earnings",
            "admin_earnings",
            "cancellation_fee_driver",
            "cancellation_fee_admin",
        ):
            assert isinstance(d[f], str), f"{f!r} should be str, got {type(d[f]).__name__}: {d[f]!r}"
        # surge_multiplier remains float (not money)
        assert isinstance(d["surge_multiplier"], float)

    def test_ride_rating_tip_is_string(self):
        m = RideRatingRequest(rating=5, tip_amount=Decimal("2.50"))
        d = _model_to_json_dict(m)
        assert isinstance(d["tip_amount"], str)
