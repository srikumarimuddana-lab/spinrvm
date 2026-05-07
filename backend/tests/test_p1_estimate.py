"""
P1: POST /rides/estimate — fare quote + signed estimate_token

The rider taps "Get fare" before confirming a ride. The handler must:
  - Return one estimate per active vehicle_type for the pickup area
  - Compute distance / duration / per-km / per-minute / surge math correctly
  - Sign an estimate_token per vehicle type so the surge shown to the rider
    is locked through to /rides confirm (P0-4 bait-and-switch guard)
  - Reject obviously bad input (out-of-range coords) before any DB hit

These tests pin:
  - Happy path: returns a list of estimates with all required fields
  - estimate_token round-trips through verify_estimate_token (signature valid)
  - surge_multiplier from service_area is preserved inside the signed token
  - Multi-stop request shape is accepted (stops are passed to airport-fee check)
  - Vehicle type variation: per-km / surge differences yield different fares
  - Invalid coords (lat > 90) → HTTPException 400 before any DB read

Run:
    pytest backend/tests/test_p1_estimate.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


RIDER_ID = "rider_p1_est"
PU_LAT, PU_LNG = 52.13, -106.67
DO_LAT, DO_LNG = 52.16, -106.65  # ~3.4 km away


def _vt(vt_id: str = "vt_standard", name: str = "Standard") -> dict:
    return {"id": vt_id, "name": name, "icon": "car"}


def _fare(
    vt_id: str = "vt_standard",
    vt_name: str = "Standard",
    *,
    base_fare: float = 3.50,
    per_km_rate: float = 1.50,
    per_minute_rate: float = 0.25,
    minimum_fare: float = 8.00,
    booking_fee: float = 2.00,
    surge_multiplier: float = 1.0,
) -> dict:
    return {
        "vehicle_type": _vt(vt_id, vt_name),
        "base_fare": base_fare,
        "per_km_rate": per_km_rate,
        "per_minute_rate": per_minute_rate,
        "minimum_fare": minimum_fare,
        "booking_fee": booking_fee,
        "surge_multiplier": surge_multiplier,
    }


class _Req:
    """Mimics RideEstimateRequest without re-importing the schema each call."""
    def __init__(
        self,
        pickup_lat: float = PU_LAT,
        pickup_lng: float = PU_LNG,
        dropoff_lat: float = DO_LAT,
        dropoff_lng: float = DO_LNG,
        stops=None,
    ):
        self.pickup_lat = pickup_lat
        self.pickup_lng = pickup_lng
        self.dropoff_lat = dropoff_lat
        self.dropoff_lng = dropoff_lng
        self.stops = stops


async def _call_estimate(
    req: _Req,
    fares: list,
    drivers: list | None = None,
    airport_fee: float = 0.0,
):
    """Invoke estimate_ride with all external deps mocked. Returns the list of
    per-vehicle-type estimate dicts."""
    from backend.routes import rides as rides_mod

    drivers = drivers if drivers is not None else []

    with (
        patch(
            "backend.routes.rides.get_fares_for_location",
            AsyncMock(return_value=fares),
        ),
        patch(
            "backend.routes.rides.db_supabase.get_rows",
            AsyncMock(return_value=drivers),
        ),
        patch(
            "backend.routes.rides.calculate_airport_fee",
            AsyncMock(return_value={
                "airport_fee": airport_fee,
                "airport_zone_name": None,
                "is_pickup": False,
                "is_dropoff": False,
                "is_stop": False,
            }),
        ),
    ):
        return await rides_mod.estimate_ride(
            request=req,  # type: ignore[arg-type]
            current_user={"id": RIDER_ID},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestEstimateHappyPath:
    """Pins estimate_ride: standard pickup/dropoff returns one estimate per
    vehicle type with all rider-app contract fields populated.

    Code under test: backend/routes/rides.py::estimate_ride (~line 444).
    """

    async def test_returns_estimate_with_all_required_fields(self):
        fares = [_fare()]
        result = await _call_estimate(_Req(), fares=fares)

        assert isinstance(result, list)
        assert len(result) == 1
        est = result[0]

        # Rider-app contract: every field below is consumed by the React Native
        # FareEstimateScreen — missing any one of them breaks the UI.
        for field in (
            "vehicle_type",
            "distance_km",
            "duration_minutes",
            "base_fare",
            "distance_fare",
            "time_fare",
            "booking_fee",
            "surge_multiplier",
            "total_fare",
            "available",
            "eta_minutes",
            "driver_count",
            "estimate_token",
        ):
            assert field in est, f"missing required field: {field}"

        # Sanity: math hangs together for a positive-distance ride.
        assert est["distance_km"] > 0
        assert est["duration_minutes"] > 0
        assert est["total_fare"] >= est["base_fare"]
        assert isinstance(est["estimate_token"], str) and est["estimate_token"]

    async def test_no_drivers_means_unavailable_with_no_eta(self):
        """When no online drivers are nearby, available=False / eta=None /
        driver_count=0 — the rider sees a 'no cars available' state."""
        result = await _call_estimate(_Req(), fares=[_fare()], drivers=[])
        est = result[0]
        assert est["available"] is False
        assert est["eta_minutes"] is None
        assert est["driver_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# P0-4 surge-lock: estimate_token must be signed and round-trip
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestEstimateTokenSigning:
    """Pins the P0-4 surge bait-and-switch guard: the token issued by /estimate
    must verify with verify_estimate_token and carry the exact surge_multiplier
    that the rider was shown."""

    async def test_token_is_signed_and_parseable(self):
        from backend.utils.estimate_token import verify_estimate_token

        fares = [_fare(surge_multiplier=1.0)]
        result = await _call_estimate(_Req(), fares=fares)
        token = result[0]["estimate_token"]

        # If the signature is wrong or coords don't match, this raises.
        payload = verify_estimate_token(
            token,
            rider_id=RIDER_ID,
            vehicle_type_id="vt_standard",
            pickup_lat=PU_LAT,
            pickup_lng=PU_LNG,
            dropoff_lat=DO_LAT,
            dropoff_lng=DO_LNG,
        )
        assert payload["rid"] == RIDER_ID
        assert payload["vt"] == "vt_standard"

    async def test_surge_multiplier_preserved_in_token(self):
        """P0-4 guard: surge shown to the rider must equal surge stored in the
        token, so /rides confirm reuses it instead of re-reading service_area."""
        from backend.utils.estimate_token import verify_estimate_token

        fares = [_fare(surge_multiplier=1.75)]
        result = await _call_estimate(_Req(), fares=fares)
        est = result[0]
        token = est["estimate_token"]

        assert est["surge_multiplier"] == 1.75

        payload = verify_estimate_token(
            token,
            rider_id=RIDER_ID,
            vehicle_type_id="vt_standard",
            pickup_lat=PU_LAT,
            pickup_lng=PU_LNG,
            dropoff_lat=DO_LAT,
            dropoff_lng=DO_LNG,
        )
        assert payload["sm"] == 1.75
        # tf in the token must equal the total_fare quoted to the rider
        assert payload["tf"] == est["total_fare"]


# ─────────────────────────────────────────────────────────────────────────────
# Multi-stop & vehicle-type variation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestEstimateRequestVariations:
    """Pins request-shape branches: stops list is forwarded for airport-fee
    detection, and per-vehicle-type fare differences propagate to the response."""

    async def test_stops_forwarded_to_airport_fee_check(self):
        """If the rider passes intermediate stops, they must be checked for
        airport-pickup surcharge — otherwise a multi-stop airport ride would
        miss the fee."""
        from backend.routes import rides as rides_mod

        airport_mock = AsyncMock(return_value={
            "airport_fee": 0.0,
            "airport_zone_name": None,
            "is_pickup": False,
            "is_dropoff": False,
            "is_stop": False,
        })

        stops = [{"address": "Mall", "lat": 52.14, "lng": -106.66}]
        req = _Req(stops=stops)

        with (
            patch(
                "backend.routes.rides.get_fares_for_location",
                AsyncMock(return_value=[_fare()]),
            ),
            patch(
                "backend.routes.rides.db_supabase.get_rows",
                AsyncMock(return_value=[]),
            ),
            patch("backend.routes.rides.calculate_airport_fee", airport_mock),
        ):
            await rides_mod.estimate_ride(
                request=req,  # type: ignore[arg-type]
                current_user={"id": RIDER_ID},
            )

        # The airport-fee check must receive the stops list verbatim so the
        # mid-trip airport leg gets the surcharge.
        _args, kwargs = airport_mock.call_args
        assert kwargs.get("stops") == stops

    async def test_airport_fee_added_to_total(self):
        """Airport surcharge from calculate_airport_fee must be added on top
        of the per-km / per-minute total — a $5 airport fee should make the
        total ~$5 higher than the same ride with no airport fee."""
        fares = [_fare()]
        no_airport = await _call_estimate(_Req(), fares=fares, airport_fee=0.0)
        with_airport = await _call_estimate(_Req(), fares=fares, airport_fee=5.0)

        assert with_airport[0]["total_fare"] > no_airport[0]["total_fare"]
        # 5.00 added → total grows by ~5.00 (no surge multiplier on airport fee)
        delta = with_airport[0]["total_fare"] - no_airport[0]["total_fare"]
        assert abs(delta - 5.0) < 0.01

    async def test_premium_vehicle_type_costs_more_than_standard(self):
        """Per-km rate variation across vehicle types must yield different
        total_fare values — riders need to see a real spread on the picker."""
        fares = [
            _fare("vt_standard", "Standard", per_km_rate=1.50, base_fare=3.50),
            _fare("vt_premium", "Premium", per_km_rate=3.00, base_fare=6.00),
        ]
        result = await _call_estimate(_Req(), fares=fares)

        assert len(result) == 2
        by_type = {est["vehicle_type"]["id"]: est for est in result}
        assert by_type["vt_premium"]["total_fare"] > by_type["vt_standard"]["total_fare"]

        # Each estimate must carry its own token so the rider's choice locks
        # the surge for *that* specific vehicle type.
        assert by_type["vt_premium"]["estimate_token"] != by_type["vt_standard"]["estimate_token"]


# ─────────────────────────────────────────────────────────────────────────────
# Input validation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.e2e
@pytest.mark.asyncio
class TestEstimateInputValidation:
    """Pins the validate_ride_location guard: out-of-range coords must 400
    before any DB or fare-engine call is issued."""

    async def test_pickup_lat_out_of_range_rejected(self):
        from backend.routes import rides as rides_mod

        # lat > 90 is geographically impossible
        bad_req = _Req(pickup_lat=999.0)

        # No mocks needed — the validator runs first and raises.
        with pytest.raises(HTTPException) as exc_info:
            await rides_mod.estimate_ride(
                request=bad_req,  # type: ignore[arg-type]
                current_user={"id": RIDER_ID},
            )

        assert exc_info.value.status_code == 400
        assert "lat" in str(exc_info.value.detail).lower()

    async def test_pickup_equals_dropoff_rejected(self):
        """Same pickup + dropoff is a misconfigured request — rider couldn't
        possibly want a 0 km ride."""
        from backend.routes import rides as rides_mod

        bad_req = _Req(
            pickup_lat=PU_LAT, pickup_lng=PU_LNG,
            dropoff_lat=PU_LAT, dropoff_lng=PU_LNG,
        )
        with pytest.raises(HTTPException) as exc_info:
            await rides_mod.estimate_ride(
                request=bad_req,  # type: ignore[arg-type]
                current_user={"id": RIDER_ID},
            )

        assert exc_info.value.status_code == 400
        assert "same" in str(exc_info.value.detail).lower()
