"""
E16: Surge boundary — multiplier changes between estimate and create

P0-4 (TestCreateRideHonorsEstimateToken) already pins the happy path:
  valid token surge=1.5 > current surge=1.0 → ride persisted at 1.5

This file pins the complementary edge cases that E16 specifically addresses:
  - No token: current surge is always applied (no protection, but no error)
  - Expired token: silently falls back to current surge, ride created OK
  - Tampered / signature-invalid token: same fallback, ride created OK
  - Token bound to wrong rider: fallback (not honoured, not 400)
  - Token bound to different route: fallback
  - Token bound to different vehicle_type: fallback

The design decision (code comment, rides.py ~L679):
  "Invalid / expired tokens fall back to current surge — we do not 400
   the request, because that would strand a rider mid-confirm if the
   token just expired. The worst case of a bad token is parity with the
   pre-token behavior (read current surge)."

Code under test: backend/routes/rides.py::create_ride (~line 685)

Run:
    pytest backend/tests/test_e16_surge_boundary.py -v
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

RIDER_ID = "rider_e16"
RIDE_ID = "ride_e16_001"

_PICKUP = dict(pickup_lat=52.13, pickup_lng=-106.67)
_DROPOFF = dict(dropoff_lat=52.12, dropoff_lng=-106.65)


def _fare_info(surge: float = 1.0) -> dict:
    return {
        "vehicle_type": {"id": "economy", "name": "Economy"},
        "base_fare": 2.5,
        "per_km_rate": 1.5,
        "per_minute_rate": 0.25,
        "booking_fee": 2.0,
        "minimum_fare": 5.0,
        "surge_multiplier": surge,
    }


def _body(token: str | None = None):
    from backend.schemas import CreateRideRequest

    return CreateRideRequest(
        vehicle_type_id="economy",
        pickup_address="123 Main St",
        **_PICKUP,
        dropoff_address="456 Broadway",
        **_DROPOFF,
        estimate_token=token,
    )


def _request():
    from starlette.requests import Request as StarletteRequest

    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/rides",
            "query_string": b"",
            "headers": [],
        }
    )


def _sign_token(
    rider_id: str = RIDER_ID,
    surge: float = 1.5,
    vehicle_type: str = "economy",
    ttl: int = 300,
    now: float | None = None,
    **coord_overrides,
) -> str:
    from backend.utils.estimate_token import sign_estimate_token

    coords = {**_PICKUP, **_DROPOFF, **coord_overrides}
    return sign_estimate_token(
        rider_id=rider_id,
        vehicle_type_id=vehicle_type,
        surge_multiplier=surge,
        total_fare=25.0,
        ttl_seconds=ttl,
        now=now,
        **coords,
    )


async def _call_create_ride(body, current_surge: float = 1.0):
    """Call create_ride with minimal mocks; returns (inserted_row_or_None, exception_or_None)."""
    from backend.routes import rides as rides_mod
    from backend.utils.rate_limiter import default_limiter

    # Reset in-memory rate-limit counters before each call so the 6 tests in
    # this class don't collectively exhaust the 5/minute ride_request_limit.
    _storage = default_limiter._storage
    if hasattr(_storage, "storage"):
        _storage.storage.clear()
    if hasattr(_storage, "events"):
        _storage.events.clear()

    rider_row = {"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}
    inserted = []

    async def _capture(row):
        inserted.append(row)
        return {**row, "id": RIDE_ID}

    try:
        with (
            patch("backend.routes.rides._deps.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=rider_row)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(
                    side_effect=[
                        [],  # no active ride
                        [],  # service_areas (airport check)
                        [{"id": "economy"}],  # vehicle_types
                    ]
                ),
            ),
            patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_fare_info(current_surge)])),
            patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
            patch(
                "backend.routes.rides._deps.calculate_all_fees",
                AsyncMock(return_value={"fees_total": 0.0, "tax_amount": 0.0}),
            ),
            patch("backend.routes.rides._deps.db_supabase.insert_ride", AsyncMock(side_effect=_capture)),
            patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
            patch(
                "backend.routes.rides._deps.db_supabase.get_ride",
                AsyncMock(return_value={"id": RIDE_ID, "status": "searching"}),
            ),
            patch("backend.routes.rides._deps.asyncio.create_task", lambda coro: coro.close()),
            patch("backend.routes.rides._deps.validate_ride_location", return_value=None),
        ):
            await rides_mod.create_ride(request=_request(), body=body, current_user={"id": RIDER_ID})
        return inserted[0] if inserted else None, None
    except Exception as exc:
        return None, exc


# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSurgeBoundaryFallbacks:
    """E16 — invalid/expired estimate_token falls back to current surge, no 400."""

    async def test_no_token_uses_current_surge(self):
        """Without an estimate_token the ride is priced at the current surge."""
        inserted, exc = await _call_create_ride(_body(token=None), current_surge=1.5)

        assert exc is None, f"Unexpected error: {exc}"
        assert inserted is not None
        assert inserted.get("surge_multiplier") == 1.5

    async def test_expired_token_falls_back_to_current_surge(self):
        """A token that has elapsed its TTL is silently ignored; current surge wins."""
        past = time.time() - 400  # 400 s ago → expired (TTL=300)
        expired_tok = _sign_token(surge=1.5, ttl=300, now=past)

        # current surge is now 1.0 — if the token were honoured we'd see 1.5
        inserted, exc = await _call_create_ride(_body(token=expired_tok), current_surge=1.0)

        assert exc is None, f"Expired token caused an error: {exc}"
        assert inserted is not None
        assert float(inserted.get("surge_multiplier", 0)) == 1.0, (
            "Expired token was honoured; should have fallen back to current surge=1.0"
        )

    async def test_tampered_token_falls_back_to_current_surge(self):
        """A token whose signature has been tampered is silently ignored."""
        valid = _sign_token(surge=1.5)
        # Corrupt the signature portion
        parts = valid.split(".")
        tampered = parts[0] + ".AAAA" + parts[1][4:]

        inserted, exc = await _call_create_ride(_body(token=tampered), current_surge=1.0)

        assert exc is None, f"Tampered token caused an error: {exc}"
        assert inserted is not None
        assert float(inserted.get("surge_multiplier", 0)) == 1.0

    async def test_wrong_rider_token_falls_back_to_current_surge(self):
        """A valid token issued to a different rider is not honoured."""
        other_tok = _sign_token(rider_id="other_rider_xyz", surge=1.5)

        inserted, exc = await _call_create_ride(_body(token=other_tok), current_surge=1.0)

        assert exc is None
        assert inserted is not None
        assert float(inserted.get("surge_multiplier", 0)) == 1.0

    async def test_wrong_vehicle_type_token_falls_back(self):
        """Token bound to a different vehicle_type is not honoured."""
        lux_tok = _sign_token(vehicle_type="luxury", surge=1.5)
        # body uses vehicle_type_id="economy" — mismatch

        inserted, exc = await _call_create_ride(_body(token=lux_tok), current_surge=1.0)

        assert exc is None
        assert inserted is not None
        assert float(inserted.get("surge_multiplier", 0)) == 1.0

    async def test_mismatched_route_token_falls_back(self):
        """Token bound to a different pickup coordinate is not honoured."""
        wrong_route_tok = _sign_token(surge=1.5, pickup_lat=51.0, pickup_lng=-105.0)

        inserted, exc = await _call_create_ride(_body(token=wrong_route_tok), current_surge=1.0)

        assert exc is None
        assert inserted is not None
        assert float(inserted.get("surge_multiplier", 0)) == 1.0
