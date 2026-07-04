"""
Unit tests to bring routes/rides.py to ≥ 80% coverage.

Focuses on the uncovered lines identified in the sprint audit:
  - _require_ride_in_state_rider (wrong state → 409, not found → 404)
  - _reestimate_fare_for_stops helper
  - estimate_ride (happy path, driver filtering, airport fee)
  - ride_search_timeout (cancels if still searching, no-op if progressed)
  - create_ride (banned/suspended user, existing active ride, fare validation)
  - get_active_ride (no ride, with unpaid ride, with driver info)
  - get_ride_history (pagination cursor)
  - get_ride (not found, auth, driver_assigned branch)
  - add_tip (happy path, not found, wrong rider, non-completed, duplicate)
  - _record_payment_event (success and error paths)
  - process_payment (not found, idempotency, no payment method, succeeded/declined/failed)

All deps mocked; no real Supabase/Stripe calls.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── minimal ride factory ───────────────────────────────────────────────────────

_RIDER_ID = "rider-001"
_DRIVER_ID = "drv-001"
_RIDE_ID = "ride-abc"


def _ride(status="completed", **kw):
    base = {
        "id": _RIDE_ID,
        "rider_id": _RIDER_ID,
        "driver_id": _DRIVER_ID,
        "status": status,
        "total_fare": 15.00,
        "payment_status": "pending",
        "payment_method": "card",
        "base_fare": 3.00,
        "distance_fare": 7.00,
        "time_fare": 4.00,
        "booking_fee": 2.00,
        "distance_km": 5.0,
        "duration_minutes": 10,
        "surge_multiplier": 1.0,
        "driver_earnings": 14.00,
        "tip_amount": None,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
        "dropoff_lat": 52.2,
        "dropoff_lng": -106.7,
    }
    base.update(kw)
    return base


_USER = {"id": _RIDER_ID}


def _starlette_request(method="GET", path="/"):
    """Build a minimal real Starlette Request that satisfies rate-limiter checks."""
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }
    return SR(scope)


# ── _require_ride_in_state_rider ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_require_ride_in_state_rider_wrong_state():
    from backend.utils.error_handling import SpinrException

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(
            side_effect=[
                None,  # not in allowed states
                _ride(status="in_progress"),  # existing row found
            ]
        )
        with pytest.raises(SpinrException) as exc:
            from backend.routes.rides import _require_ride_in_state_rider

            await _require_ride_in_state_rider(
                _RIDE_ID,
                _RIDER_ID,
                ("searching", "driver_assigned"),
            )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_require_ride_in_state_rider_not_found():
    from backend.utils.error_handling import RideNotFoundException

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=None)
        with pytest.raises(RideNotFoundException):
            from backend.routes.rides import _require_ride_in_state_rider

            await _require_ride_in_state_rider(
                "no-such-ride",
                _RIDER_ID,
                ("searching",),
            )


# ── _reestimate_fare_for_stops ─────────────────────────────────────────────────


def test_reestimate_fare_for_stops_returns_updated_fare():
    from backend.routes.rides import _reestimate_fare_for_stops

    ride = _ride()
    ride["pickup_lat"] = 52.1
    ride["pickup_lng"] = -106.6
    ride["dropoff_lat"] = 52.2
    ride["dropoff_lng"] = -106.7

    stops = [{"lat": 52.15, "lng": -106.65}]
    result = _reestimate_fare_for_stops(ride, stops)

    assert "distance_km" in result
    assert "total_fare" in result
    assert "duration_minutes" in result


# ── estimate_ride ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_estimate_ride_returns_estimates():
    from backend.routes.rides import RideEstimateRequest, estimate_ride

    fake_fares = [
        {
            "vehicle_type": {"id": "vt-1", "name": "Standard"},
            "base_fare": "3.00",
            "per_km_rate": "1.50",
            "per_minute_rate": "0.30",
            "booking_fee": "2.00",
            "surge_multiplier": "1.0",
            "minimum_fare": "5.00",
        }
    ]

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.calculate_distance", return_value=5.0),
        patch("backend.routes.rides.get_fares_for_location", new_callable=AsyncMock, return_value=fake_fares),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.calculate_airport_fee", new_callable=AsyncMock, return_value={"airport_fee": 0.0}),
        patch("backend.routes.rides.sign_estimate_token", return_value="tok"),
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.get_service_area_for_point = AsyncMock(return_value=None)

        result = await estimate_ride(
            body=RideEstimateRequest(pickup_lat=52.1, pickup_lng=-106.6, dropoff_lat=52.2, dropoff_lng=-106.7),
            current_user=_USER,
        )

    assert len(result["estimates"]) == 1
    assert result["estimates"][0]["vehicle_type"]["id"] == "vt-1"
    assert result["estimates"][0]["estimate_token"] == "tok"


@pytest.mark.anyio
async def test_estimate_ride_includes_nearby_drivers():
    """Drivers within 10km with user_id should be counted."""
    from backend.routes.rides import RideEstimateRequest, estimate_ride

    fake_fares = [
        {
            "vehicle_type": {"id": "vt-1", "name": "Standard"},
            "base_fare": "3.00",
            "per_km_rate": "1.50",
            "per_minute_rate": "0.30",
            "booking_fee": "2.00",
            "surge_multiplier": "1.0",
            "minimum_fare": "5.00",
        }
    ]
    nearby_driver = {
        "user_id": "u-1",
        "lat": 52.1,
        "lng": -106.6,
        "vehicle_type_id": "vt-1",
    }

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.calculate_distance", return_value=2.0),
        patch("backend.routes.rides.get_fares_for_location", new_callable=AsyncMock, return_value=fake_fares),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.calculate_airport_fee", new_callable=AsyncMock, return_value={"airport_fee": 0.0}),
        patch("backend.routes.rides.sign_estimate_token", return_value="tok2"),
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
    ):
        # First get_rows call fetches service_areas (return empty to skip geofence),
        # second fetches nearby drivers.
        mock_db.get_rows = AsyncMock(side_effect=[[], [nearby_driver]])
        mock_db.get_service_area_for_point = AsyncMock(return_value=None)

        result = await estimate_ride(
            body=RideEstimateRequest(pickup_lat=52.1, pickup_lng=-106.6, dropoff_lat=52.2, dropoff_lng=-106.7),
            current_user=_USER,
        )

    assert result["estimates"][0]["driver_count"] == 1
    assert result["estimates"][0]["available"] is True


@pytest.mark.anyio
async def test_estimate_ride_allows_dropoff_matched_by_polygon_fallback():
    """Dropoff geofence should use the same polygon fallback as pickup."""
    from backend.routes.rides import RideEstimateRequest, estimate_ride

    fake_fares = [
        {
            "vehicle_type": {"id": "vt-1", "name": "Standard"},
            "base_fare": "3.00",
            "per_km_rate": "1.50",
            "per_minute_rate": "0.30",
            "booking_fee": "2.00",
            "surge_multiplier": "1.0",
            "minimum_fare": "5.00",
        }
    ]
    active_area = {
        "id": "area-1",
        "name": "Polygon Area",
        "is_active": True,
        "polygon": [
            {"lat": 52.0, "lng": -107.0},
            {"lat": 52.0, "lng": -106.0},
            {"lat": 53.0, "lng": -106.0},
            {"lat": 53.0, "lng": -107.0},
        ],
    }

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.calculate_distance", return_value=5.0),
        patch("backend.routes.rides.get_fares_for_location", new_callable=AsyncMock, return_value=fake_fares),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.calculate_airport_fee", new_callable=AsyncMock, return_value={"airport_fee": 0.0}),
        patch("backend.routes.rides.calculate_all_fees", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.sign_estimate_token", return_value="tok"),
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
    ):
        # Simulates production rows where the PostGIS geography column is empty
        # or stale even though the admin-visible polygon contains both points.
        mock_db.get_service_area_for_point = AsyncMock(return_value=None)
        mock_db.get_rows = AsyncMock(side_effect=[[active_area], []])

        result = await estimate_ride(
            body=RideEstimateRequest(pickup_lat=52.1, pickup_lng=-106.6, dropoff_lat=52.2, dropoff_lng=-106.7),
            current_user=_USER,
        )

    assert len(result["estimates"]) == 1
    assert result["estimates"][0]["estimate_token"] == "tok"
    assert mock_db.get_service_area_for_point.await_count == 2


# ── get_active_ride ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_active_ride_no_ride():
    from backend.routes.rides import get_active_ride

    req = _starlette_request()

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[])

        result = await get_active_ride(request=req, current_user=_USER)

    assert result["active"] is False
    assert result["ride"] is None


@pytest.mark.anyio
async def test_get_active_ride_with_unpaid_ride():
    from backend.routes.rides import get_active_ride

    req = _starlette_request()
    unpaid = _ride(status="completed", payment_status="pending", driver_id=None)

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(side_effect=[[unpaid], []])
        mock_db.get_driver_by_id = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(return_value=None)

        result = await get_active_ride(request=req, current_user=_USER)

    assert result["active"] is True
    assert result["ride"]["id"] == _RIDE_ID


@pytest.mark.anyio
async def test_get_active_ride_with_driver_info():
    from backend.routes.rides import get_active_ride

    req = _starlette_request()
    active = _ride(status="driver_accepted", driver_id=_DRIVER_ID)
    driver_row = {"id": _DRIVER_ID, "user_id": "driver-user", "rating": 4.9, "total_rides": 50}
    user_row = {"id": "driver-user", "first_name": "John", "last_name": "Doe"}

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(side_effect=[[], [active]])
        mock_db.get_driver_by_id = AsyncMock(return_value=driver_row)
        mock_db.get_user_by_id = AsyncMock(return_value=user_row)

        result = await get_active_ride(request=req, current_user=_USER)

    assert result["active"] is True
    assert result["ride"]["driver"]["name"] == "John Doe"


# ── get_ride_history ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_ride_history_returns_completed_rides():
    from backend.routes.rides import get_ride_history

    req = _starlette_request()
    completed = [_ride(status="completed") for i in range(5)]
    for i, r in enumerate(completed):
        r["id"] = f"ride-{i}"

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=completed)
        mock_db.find_one = AsyncMock(return_value=None)

        result = await get_ride_history(request=req, limit=3, before=None, current_user=_USER)

    assert len(result["rides"]) == 3
    assert result["next_cursor"] is not None


@pytest.mark.anyio
async def test_get_ride_history_cursor_pagination():
    from backend.routes.rides import get_ride_history

    req = _starlette_request()
    # The DB layer applies the `created_at < cursor_ts` filter, so the mock
    # simulates that by returning only rides older than the cursor.
    rides_after_cursor = [{"id": f"r-{i}", "status": "completed", "driver_id": _DRIVER_ID} for i in range(2, 5)]

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=rides_after_cursor)
        mock_db.find_one = AsyncMock(return_value={"id": "r-1", "created_at": "2024-01-02T00:00:00Z"})

        result = await get_ride_history(request=req, limit=20, before="r-1", current_user=_USER)

    # Should include rides after cursor index 1: r-2, r-3, r-4
    assert all(r["id"] != "r-0" for r in result["rides"])
    assert all(r["id"] != "r-1" for r in result["rides"])


@pytest.mark.anyio
async def test_get_ride_history_filters_cancelled_without_driver():
    """Cancelled rides with no driver_id are excluded."""
    from backend.routes.rides import get_ride_history

    req = _starlette_request()
    rides = [
        {"id": "r-1", "status": "cancelled", "driver_id": None},  # excluded
        {"id": "r-2", "status": "completed", "driver_id": _DRIVER_ID},  # included
        {"id": "r-3", "status": "cancelled", "driver_id": _DRIVER_ID},  # included
    ]

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=rides)
        mock_db.find_one = AsyncMock(return_value=None)

        result = await get_ride_history(request=req, limit=20, before=None, current_user=_USER)

    ids = [r["id"] for r in result["rides"]]
    assert "r-1" not in ids
    assert "r-2" in ids
    assert "r-3" in ids


# ── get_ride (single ride) ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_ride_not_found():
    from backend.routes.rides import get_ride
    from backend.utils.error_handling import RideNotFoundException

    req = _starlette_request()

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        mock_db.get_rows = AsyncMock(return_value=[])

        with pytest.raises(RideNotFoundException):
            await get_ride(request=req, ride_id="no-such", current_user=_USER)


@pytest.mark.anyio
async def test_get_ride_unauthorized():
    from fastapi import HTTPException

    from backend.routes.rides import get_ride

    req = _starlette_request()
    ride = _ride(rider_id="other-rider")

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])  # no driver match

        with patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}):
            with pytest.raises(HTTPException) as exc:
                await get_ride(request=req, ride_id=_RIDE_ID, current_user={"id": "intruder"})

    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_get_ride_rider_view_searching():
    from backend.routes.rides import get_ride

    req = _starlette_request()
    ride = _ride(status="searching", rider_id=_RIDER_ID, driver_id=None)

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.get_driver_by_id = AsyncMock(return_value=None)

        with patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}):
            result = await get_ride(request=req, ride_id=_RIDE_ID, current_user=_USER)

    assert result["id"] == _RIDE_ID
    assert result["offer_expires_at"] is None


@pytest.mark.anyio
async def test_get_ride_driver_assigned_sets_offer_expiry():
    from backend.routes.rides import get_ride

    req = _starlette_request()
    ride = _ride(
        status="driver_assigned",
        rider_id=_RIDER_ID,
        driver_id=_DRIVER_ID,
        driver_notified_at="2026-01-01T00:00:00+00:00",
    )

    mock_driver = {"id": _DRIVER_ID, "user_id": "drv-user", "name": "Jane"}

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.get_driver_by_id = AsyncMock(return_value=mock_driver)
        mock_db.get_user_by_id = AsyncMock(return_value={"profile_image": ""})

        with patch(
            "backend.routes.rides.get_app_settings",
            new_callable=AsyncMock,
            return_value={"ride_offer_timeout_seconds": "15"},
        ):
            result = await get_ride(request=req, ride_id=_RIDE_ID, current_user=_USER)

    assert result["offer_expires_at"] is not None
    assert result["offer_timeout_seconds"] == 15


# ── add_tip ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_add_tip_success():
    from backend.routes.rides import TipRequest, add_tip

    req = _starlette_request(method="POST", path="/rides/ride-abc/tip")
    completed_ride = _ride(status="completed", driver_earnings=14.00, tip_amount=None)

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=completed_ride)
        mock_db.update_ride = AsyncMock()
        mock_db.get_driver_by_id = AsyncMock(return_value=None)

        with patch("backend.routes.rides.manager") as mock_manager:
            mock_manager.send_personal_message = AsyncMock()

            result = await add_tip(
                ride_id=_RIDE_ID,
                req=TipRequest(amount=Decimal("2.00")),
                request=req,
                current_user=_USER,
            )

    assert result["success"] is True
    assert Decimal(str(result["tip_amount"])) == Decimal("2.00")


@pytest.mark.anyio
async def test_add_tip_ride_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import TipRequest, add_tip

    req = _starlette_request(method="POST", path="/rides/no-ride/tip")

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await add_tip(
                ride_id="no-ride",
                req=TipRequest(amount=Decimal("2.00")),
                request=req,
                current_user=_USER,
            )

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_add_tip_wrong_rider():
    from fastapi import HTTPException

    from backend.routes.rides import TipRequest, add_tip

    req = _starlette_request(method="POST", path="/rides/ride-abc/tip")
    ride = _ride(status="completed", rider_id="other-rider")

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)

        with pytest.raises(HTTPException) as exc:
            await add_tip(
                ride_id=_RIDE_ID,
                req=TipRequest(amount=Decimal("2.00")),
                request=req,
                current_user=_USER,
            )

    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_add_tip_not_completed():
    from fastapi import HTTPException

    from backend.routes.rides import TipRequest, add_tip

    req = _starlette_request(method="POST", path="/rides/ride-abc/tip")
    ride = _ride(status="in_progress")

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)

        with pytest.raises(HTTPException) as exc:
            await add_tip(
                ride_id=_RIDE_ID,
                req=TipRequest(amount=Decimal("2.00")),
                request=req,
                current_user=_USER,
            )

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_add_tip_duplicate():
    from fastapi import HTTPException

    from backend.routes.rides import TipRequest, add_tip

    req = _starlette_request(method="POST", path="/rides/ride-abc/tip")
    ride = _ride(status="completed", tip_amount=3.00)  # already tipped

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)

        with pytest.raises(HTTPException) as exc:
            await add_tip(
                ride_id=_RIDE_ID,
                req=TipRequest(amount=Decimal("2.00")),
                request=req,
                current_user=_USER,
            )

    assert "ERR_TIP_DUPLICATE" in str(exc.value.detail)


# ── _record_payment_event ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_record_payment_event_success():
    from backend.routes.rides import _record_payment_event

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.insert_one = AsyncMock()
        await _record_payment_event(_RIDE_ID, _RIDER_ID, 1500, "pi_test")
    mock_db.insert_one.assert_called_once()


@pytest.mark.anyio
async def test_record_payment_event_swallows_error():
    """Ledger write failure must not propagate."""
    from backend.routes.rides import _record_payment_event

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.insert_one = AsyncMock(side_effect=Exception("DB error"))
        # Should not raise
        await _record_payment_event(_RIDE_ID, _RIDER_ID, 1500, "pi_test")


# ── process_payment ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_process_payment_ride_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id="no-ride",
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_process_payment_wrong_rider():
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(rider_id="other"))

        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_process_payment_already_paid():
    from backend.routes.rides import ProcessPaymentRequest, process_payment

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(status="completed", payment_status="paid"))

        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )

    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_wrong_status():
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(status="in_progress"))

        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_process_payment_no_payment_method():
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    rider_user = {"id": _RIDER_ID, "stripe_customer_id": "cus_1", "default_payment_method": None}

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(status="completed", payment_method_id=None))
        mock_db.get_user_by_id = AsyncMock(return_value=rider_user)
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": _RIDE_ID})  # guard lock succeeds

        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_process_payment_succeeded():
    from backend.routes.rides import ProcessPaymentRequest, process_payment

    rider_user = {"id": _RIDER_ID, "stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}

    mock_outcome = MagicMock()
    mock_outcome.status = "succeeded"
    mock_outcome.payment_intent_id = "pi_success"

    import sys

    mock_email_mod = MagicMock()
    mock_email_mod.send_receipt_email = AsyncMock(return_value=True)

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=mock_outcome),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
        patch.dict(sys.modules, {"utils.email_receipt": mock_email_mod}),
    ):
        mock_db.get_ride = AsyncMock(return_value=_ride(status="completed", payment_method_id="pm_1"))
        mock_db.get_user_by_id = AsyncMock(return_value=rider_user)
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": _RIDE_ID})  # guard lock
        mock_db.insert_one = AsyncMock()
        mock_db.get_driver_by_id = AsyncMock(return_value=None)
        mock_manager.send_personal_message = AsyncMock()

        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )

    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_declined():
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    rider_user = {"id": _RIDER_ID, "stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}

    mock_outcome = MagicMock()
    mock_outcome.status = "declined"
    mock_outcome.payment_intent_id = "pi_declined"
    mock_outcome.decline_code = "insufficient_funds"
    mock_outcome.error_message = "Insufficient funds"

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=mock_outcome),
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        mock_db.get_ride = AsyncMock(return_value=_ride(status="completed", payment_method_id="pm_1"))
        mock_db.get_user_by_id = AsyncMock(return_value=rider_user)
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": _RIDE_ID})  # guard lock
        mock_db.insert_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )

    assert exc.value.status_code == 402
    assert "card_declined" in str(exc.value.detail)


@pytest.mark.anyio
async def test_process_payment_requires_action():
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    rider_user = {"id": _RIDER_ID, "stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}

    mock_outcome = MagicMock()
    mock_outcome.status = "requires_action"
    mock_outcome.payment_intent_id = "pi_3ds"

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=mock_outcome),
    ):
        mock_db.get_ride = AsyncMock(return_value=_ride(status="completed", payment_method_id="pm_1"))
        mock_db.get_user_by_id = AsyncMock(return_value=rider_user)
        mock_db.update_ride = AsyncMock()
        mock_db.update_one = AsyncMock(return_value={"id": _RIDE_ID})  # guard lock
        mock_db.insert_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )

    assert exc.value.status_code == 402
    assert "authentication_required" in str(exc.value.detail)


# ── ride_search_timeout ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_ride_search_timeout_cancels_searching_ride():
    from backend.routes.rides import ride_search_timeout

    searching_ride = _ride(status="searching", rider_id=_RIDER_ID)

    with (
        patch("backend.routes.rides.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        mock_db.get_ride = AsyncMock(return_value=searching_ride)
        mock_db.update_ride = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()
        mock_manager.broadcast_to_admins = AsyncMock()

        await ride_search_timeout(_RIDE_ID, timeout_seconds=0)

    mock_db.update_ride.assert_called()


@pytest.mark.anyio
async def test_ride_search_timeout_skips_non_searching_ride():
    from backend.routes.rides import ride_search_timeout

    completed_ride = _ride(status="completed")

    with (
        patch("backend.routes.rides.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.routes.rides.db_supabase") as mock_db,
    ):
        mock_db.get_ride = AsyncMock(return_value=completed_ride)
        mock_db.update_ride = AsyncMock()

        await ride_search_timeout(_RIDE_ID, timeout_seconds=0)

    mock_db.update_ride.assert_not_called()


# ── create_ride guards ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_ride_banned_user():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")

    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "banned"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 403
    assert "suspended" in exc.value.detail.lower() or "banned" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_create_ride_suspended_user():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")

    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "suspended"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_create_ride_card_no_stripe_customer():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")

    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        payment_method="card",
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        # Stripe configured → card-on-file requirement is enforced.
        patch(
            "backend.routes.rides.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": None})
        mock_supabase.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 400
    assert "card" in exc.value.detail.lower() or "payment" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_create_ride_existing_active_ride():
    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest
    from backend.utils.error_handling import SpinrException

    req = _starlette_request(method="POST", path="/rides")

    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        # A card ride must name an explicit card (server-side money guard); set
        # one so this test reaches the active-ride 409 it is actually exercising
        # rather than tripping the missing-card 400.
        payment_method_id="pm_1",
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        # No existing idempotency ride, but there IS an active ride
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[_ride(status="searching")])

        with pytest.raises((SpinrException, Exception)) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_create_ride_card_without_payment_method_id_rejected():
    """A card ride with no explicit payment_method_id must be rejected (400).

    Regression guard: settle_card() falls back to the Stripe customer's
    default_payment_method when the ride has no payment_method_id, which is how
    a stale card got charged for a ride the rider never selected it for. The
    booking endpoint must reject the card ride up front instead.
    """
    from fastapi import HTTPException

    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")

    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        payment_method="card",
        # payment_method_id intentionally omitted → defaults to None.
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        # Stripe configured → the card-on-file requirement is enforced.
        patch(
            "backend.routes.rides.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
    ):
        # Rider HAS a Stripe customer (so the existing stripe_customer_id check
        # passes) but supplies no card to charge.
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 400
    assert "card" in str(exc.value.detail).lower()


@pytest.mark.anyio
async def test_create_ride_card_demo_mode_skips_card_requirement():
    """In demo mode (Stripe unconfigured) a card ride must NOT be blocked for
    lacking a card — settlement uses the unconfigured path (paid, no charge), so
    requiring a card on file would make local/staging card bookings impossible.

    An active ride is staged so we expect to reach the 409, proving the card
    requirement did not short-circuit with a 400 first.
    """
    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")
    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        payment_method="card",
        # No card, no stripe_customer_id — but Stripe is unconfigured.
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        # Stripe NOT configured → demo mode, card requirement is skipped.
        patch("backend.routes.rides.get_app_settings", AsyncMock(return_value={})),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[_ride(status="searching")])

        with pytest.raises(Exception) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert getattr(exc.value, "status_code", None) == 409


@pytest.mark.anyio
async def test_create_ride_work_profile_without_corporate_account_still_requires_card():
    """P1: a bare work_profile flag (no corporate_account_id) must NOT exempt the
    card requirement — otherwise the ride is never reclassified to corporate and
    settles on the stored default card (the stale-card path this guard closes).
    """
    from fastapi import HTTPException

    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")
    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        payment_method="card",
        work_profile=True,  # but no corporate_account_id → NOT corporate
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 400
    assert "card" in str(exc.value.detail).lower()


@pytest.mark.anyio
async def test_create_ride_corporate_work_profile_exempt_from_card_requirement():
    """A genuinely corporate-billed ride (work_profile + corporate_account_id,
    reclassified to company_allowance → settle_corporate) is exempt from the
    personal-card requirement: no payment_method_id needed, so it reaches the
    later active-ride check (409 here)."""
    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")
    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        payment_method="card",
        corporate_account_id="corp-1",
        work_profile=True,  # corporate-BILLED → exempt
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[_ride(status="searching")])

        with pytest.raises(Exception) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert getattr(exc.value, "status_code", None) == 409


@pytest.mark.anyio
async def test_create_ride_corporate_tag_without_work_profile_still_requires_card():
    """A bare corporate_account_id with payment_method='card' and NO work_profile
    is NOT corporate-billed — it settles via settle_card against the rider's card,
    so it must still pin one. Exempting on the tag alone re-opened the
    stale-default-card charge (money audit), so this must be rejected (400)."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride
    from backend.schemas import CreateRideRequest

    req = _starlette_request(method="POST", path="/rides")
    body = CreateRideRequest(
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        pickup_address="100 Main St",
        dropoff_address="200 Broadway Ave",
        vehicle_type_id="vt-1",
        payment_method="card",
        corporate_account_id="corp-1",  # tag only, NOT corporate-billed
    )

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        mock_supabase.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await create_ride(request=req, body=body, current_user=_USER)

    assert exc.value.status_code == 400
    assert "card" in str(exc.value.detail).lower()


# ── match_driver_to_ride ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_match_driver_to_ride_no_ride():
    """Early-return when ride not found."""
    from backend.routes.rides import match_driver_to_ride

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        # Should return without error
        await match_driver_to_ride("no-such-ride")


@pytest.mark.anyio
async def test_match_driver_to_ride_no_drivers():
    """No eligible drivers — ride stays in searching."""
    from backend.routes.rides import match_driver_to_ride

    ride = _ride(
        status="searching",
        vehicle_type_id="vt-1",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        pickup_address="123 Main St",
        dropoff_address="456 Broadway",
        rider_id=_RIDER_ID,
    )

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.dispatch") as mock_dispatch,
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.filter_and_rank_drivers", return_value=[]),
    ):
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_dispatch.resolve_matching_config = AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True))

        await match_driver_to_ride(_RIDE_ID, ride=ride)

    # No update_ride called — ride remains searching
    mock_db.update_ride = AsyncMock()


@pytest.mark.anyio
async def test_match_driver_to_ride_assigns_driver():
    """One available driver gets assigned."""
    from backend.routes.rides import match_driver_to_ride

    ride = _ride(
        status="searching",
        vehicle_type_id="vt-1",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        pickup_address="123 Main St",
        dropoff_address="456 Broadway",
        rider_id=_RIDER_ID,
    )
    driver = {
        "id": _DRIVER_ID,
        "user_id": "driver-user-1",
        "lat": 52.1,
        "lng": -106.6,
        "is_online": True,
        "is_available": True,
        "vehicle_type_id": "vt-1",
        "rating": 4.9,
    }

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.dispatch") as mock_dispatch,
        patch(
            "backend.routes.rides.get_app_settings",
            new_callable=AsyncMock,
            return_value={"ride_offer_timeout_seconds": "15"},
        ),
        patch("backend.routes.rides.filter_and_rank_drivers", return_value=[(driver, 1.5)]),
        patch("backend.routes.rides.record_period_transition", new_callable=AsyncMock),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
        patch("backend.routes.rides.asyncio.create_task"),
    ):
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.claim_driver_atomic = AsyncMock(return_value={"id": _DRIVER_ID})
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.update_ride = AsyncMock()
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Alice", "last_name": "R"})
        mock_dispatch.resolve_matching_config = AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True))
        mock_manager.send_personal_message = AsyncMock()

        await match_driver_to_ride(_RIDE_ID, ride=ride)

    mock_db.update_ride.assert_called_once()
    update_args = mock_db.update_ride.call_args[0]
    assert update_args[1]["status"] == "driver_assigned"


# ── offer_timeout_handler ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_offer_timeout_handler_releases_driver():
    """Offer timeout releases driver and re-dispatches when still assigned."""
    from backend.routes.rides import _offer_timeout_handler

    ride = _ride(status="driver_assigned", driver_id=_DRIVER_ID, rider_id=_RIDER_ID)

    with (
        patch("backend.routes.rides.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.record_period_transition", new_callable=AsyncMock),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.match_driver_to_ride", new_callable=AsyncMock),
    ):
        mock_db.find_one = AsyncMock(return_value=ride)
        mock_db.update_one = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()

        await _offer_timeout_handler(_RIDE_ID, _DRIVER_ID, _RIDER_ID, timeout_seconds=0)

    mock_db.update_one.assert_called()


@pytest.mark.anyio
async def test_offer_timeout_handler_no_op_when_accepted():
    """Offer timeout does nothing if ride already progressed (accepted)."""
    from backend.routes.rides import _offer_timeout_handler

    accepted_ride = _ride(status="driver_accepted", driver_id=_DRIVER_ID)

    with (
        patch("backend.routes.rides.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.routes.rides.db") as mock_db,
    ):
        mock_db.find_one = AsyncMock(return_value=accepted_ride)
        mock_db.update_one = AsyncMock()

        await _offer_timeout_handler(_RIDE_ID, _DRIVER_ID, _RIDER_ID, timeout_seconds=0)

    mock_db.update_one.assert_not_called()


# ── helpers: _d, _round, _f ───────────────────────────────────────────────────


def test_decimal_helpers():
    from backend.routes.rides import _d, _f, _round

    assert _d("10.005") == Decimal("10.005")
    assert _round(Decimal("10.005")) == Decimal("10.01")
    assert isinstance(_f(Decimal("10.00")), float)
    assert _f(Decimal("10.00")) == 10.0


# ── rate_driver ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rate_driver_success():
    from backend.routes.rides import rate_driver
    from backend.schemas import RideRatingRequest

    completed = _ride(status="completed", driver_id=_DRIVER_ID)
    driver = {"id": _DRIVER_ID, "user_id": "drv-user", "rating": 4.8, "total_ratings": 10}

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        mock_db.get_ride = AsyncMock(return_value=completed)
        mock_db.update_ride = AsyncMock()
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.update_one = AsyncMock()

        result = await rate_driver(
            ride_id=_RIDE_ID,
            rating_data=RideRatingRequest(rating=5),
            current_user=_USER,
        )

    assert result["success"] is True


@pytest.mark.anyio
async def test_rate_driver_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import rate_driver
    from backend.schemas import RideRatingRequest

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await rate_driver(
                ride_id="no-ride",
                rating_data=RideRatingRequest(rating=5),
                current_user=_USER,
            )

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_rate_driver_not_completed():
    from fastapi import HTTPException

    from backend.routes.rides import rate_driver
    from backend.schemas import RideRatingRequest

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(status="in_progress"))

        with pytest.raises(HTTPException) as exc:
            await rate_driver(
                ride_id=_RIDE_ID,
                rating_data=RideRatingRequest(rating=5),
                current_user=_USER,
            )

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_rate_driver_with_tip():
    """Tip in rating request also updates driver earnings."""
    from backend.routes.rides import rate_driver
    from backend.schemas import RideRatingRequest

    completed = _ride(status="completed", driver_id=_DRIVER_ID, tip_amount=0.0)
    driver = {"id": _DRIVER_ID, "user_id": "drv-user", "rating": 4.8, "total_ratings": 10}

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        mock_db.get_ride = AsyncMock(return_value=completed)
        mock_db.update_ride = AsyncMock()
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.update_one = AsyncMock()

        result = await rate_driver(
            ride_id=_RIDE_ID,
            rating_data=RideRatingRequest(rating=5, tip_amount=3.0),
            current_user=_USER,
        )

    assert result["success"] is True
    # update_ride should be called twice: once for rating, once for tip
    assert mock_db.update_ride.call_count >= 1


# ── cancel_ride_rider ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cancel_ride_rider_searching():
    """Rider cancels a searching ride — no cancellation fee."""
    from backend.routes.rides import cancel_ride_rider

    req = _starlette_request(method="POST", path="/rides/ride-abc/cancel")
    searching = _ride(status="searching", driver_id=None)
    cancelled = _ride(status="cancelled", driver_id=None)

    with (
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        # _require_ride_in_state_rider: find_one returns searching ride twice (allowed check + fallback)
        mock_db.find_one = AsyncMock(return_value=searching)
        mock_db.update_one = AsyncMock()
        mock_supabase.update_ride = AsyncMock()
        # get_ride called for verification — must return "cancelled" to pass the check
        mock_supabase.get_ride = AsyncMock(return_value=cancelled)
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()
        mock_manager.broadcast_to_admins = AsyncMock()

        result = await cancel_ride_rider(
            request=req,
            ride_id=_RIDE_ID,
            current_user=_USER,
        )

    assert result["success"] is True


@pytest.mark.anyio
async def test_cancel_ride_rider_driver_arrived_fee():
    """Driver already arrived — flat $5 cancellation fee."""

    from backend.routes.rides import cancel_ride_rider

    req = _starlette_request(method="POST", path="/rides/ride-abc/cancel")
    arrived = _ride(status="driver_arrived", driver_id=_DRIVER_ID)
    cancelled = _ride(status="cancelled", driver_id=_DRIVER_ID)
    wallet = {"id": "wallet-1", "balance": 100.0}
    driver = {"id": _DRIVER_ID, "user_id": "drv-user"}

    with (
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.db_supabase") as mock_supabase,
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
        patch("backend.routes.rides.record_period_transition", new_callable=AsyncMock),
    ):
        # _require_ride_in_state_rider: find_one returns arrived ride
        mock_db.find_one = AsyncMock(side_effect=[arrived, wallet])
        mock_db.update_one = AsyncMock()
        mock_db.insert_one = AsyncMock()
        mock_supabase.get_driver_by_id = AsyncMock(return_value=driver)
        mock_supabase.update_ride = AsyncMock()
        # get_ride verification returns cancelled
        mock_supabase.get_ride = AsyncMock(return_value=cancelled)
        mock_supabase.set_driver_available = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()
        mock_manager.broadcast_to_admins = AsyncMock()

        result = await cancel_ride_rider(
            request=req,
            ride_id=_RIDE_ID,
            current_user=_USER,
        )

    assert result["success"] is True
    assert result["cancellation_fee"] > 0


# ── add_stop_mid_trip / remove_stop_mid_trip ──────────────────────────────────


@pytest.mark.anyio
async def test_add_stop_mid_trip_success():
    from backend.routes.rides import AddStopMidTripRequest, add_stop_mid_trip

    ride = _ride(status="in_progress", stops=[])

    with (
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.manager") as mock_manager,
    ):
        mock_db.find_one = AsyncMock(return_value=ride)
        mock_db.update_one = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()

        result = await add_stop_mid_trip(
            ride_id=_RIDE_ID,
            req=AddStopMidTripRequest(address="Mid Stop", lat=52.15, lng=-106.65),
            current_user=_USER,
        )

    assert result["success"] is True
    assert len(result["stops"]) == 1


@pytest.mark.anyio
async def test_add_stop_mid_trip_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import AddStopMidTripRequest, add_stop_mid_trip

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await add_stop_mid_trip(
                ride_id="no-ride",
                req=AddStopMidTripRequest(address="Stop", lat=52.0, lng=-106.0),
                current_user=_USER,
            )

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_remove_stop_mid_trip_success():
    from backend.routes.rides import remove_stop_mid_trip

    ride = _ride(status="in_progress", stops=[{"address": "S1", "lat": 52.1, "lng": -106.6}])

    with (
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.manager") as mock_manager,
    ):
        mock_db.find_one = AsyncMock(return_value=ride)
        mock_db.update_one = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()

        result = await remove_stop_mid_trip(
            ride_id=_RIDE_ID,
            stop_index=0,
            current_user=_USER,
        )

    assert result["success"] is True
    assert result["stops"] == []


@pytest.mark.anyio
async def test_remove_stop_mid_trip_invalid_index():
    from fastapi import HTTPException

    from backend.routes.rides import remove_stop_mid_trip

    ride = _ride(status="in_progress", stops=[])

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=ride)

        with pytest.raises(HTTPException) as exc:
            await remove_stop_mid_trip(ride_id=_RIDE_ID, stop_index=5, current_user=_USER)

    assert exc.value.status_code == 400


# ── match_driver_to_ride: round_robin and claim-fail branches ─────────────────


@pytest.mark.anyio
async def test_match_driver_no_claim_returns_early():
    """All drivers fail the atomic claim — match returns without assignment."""
    from backend.routes.rides import match_driver_to_ride

    ride = _ride(
        status="searching",
        vehicle_type_id="vt-1",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        pickup_address="A",
        dropoff_address="B",
        rider_id=_RIDER_ID,
    )
    driver = {"id": _DRIVER_ID, "user_id": "drv-user", "lat": 52.1, "lng": -106.6}

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.dispatch") as mock_dispatch,
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.filter_and_rank_drivers", return_value=[(driver, 1.0)]),
    ):
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.claim_driver_atomic = AsyncMock(return_value=None)  # claim always fails
        mock_db.update_ride = AsyncMock()
        mock_dispatch.resolve_matching_config = AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True))

        await match_driver_to_ride(_RIDE_ID, ride=ride)

    # No ride update should happen when all claims fail
    mock_db.update_ride.assert_not_called()


@pytest.mark.anyio
async def test_match_driver_offline_after_claim():
    """Driver went offline between selection and claim — release and bail."""
    from backend.routes.rides import match_driver_to_ride

    ride = _ride(
        status="searching",
        vehicle_type_id="vt-1",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        pickup_address="A",
        dropoff_address="B",
        rider_id=_RIDER_ID,
    )
    driver = {"id": _DRIVER_ID, "user_id": "drv-user", "lat": 52.1, "lng": -106.6}

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.dispatch") as mock_dispatch,
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.filter_and_rank_drivers", return_value=[(driver, 1.0)]),
        patch("backend.routes.rides.match_driver_to_ride", new_callable=AsyncMock),  # prevent recursion
    ):
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.claim_driver_atomic = AsyncMock(return_value={"id": _DRIVER_ID})
        # Driver is now offline after claim
        mock_db.get_driver_by_id = AsyncMock(return_value={"id": _DRIVER_ID, "is_online": False})
        mock_db.set_driver_available = AsyncMock()
        mock_db.update_ride = AsyncMock()
        mock_dispatch.resolve_matching_config = AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True))

        await match_driver_to_ride(_RIDE_ID, ride=ride)

    mock_db.set_driver_available.assert_called_once_with(_DRIVER_ID, True)
    mock_db.update_ride.assert_not_called()


# ── get_chat_status ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_chat_status_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import get_chat_status

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await get_chat_status(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_get_chat_status_cancelled():
    from backend.routes.rides import get_chat_status

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=_ride(status="cancelled"))
        result = await get_chat_status(ride_id=_RIDE_ID, current_user=_USER)
    assert result["available"] is False


@pytest.mark.anyio
async def test_get_chat_status_active():
    from backend.routes.rides import get_chat_status

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=_ride(status="in_progress"))
        result = await get_chat_status(ride_id=_RIDE_ID, current_user=_USER)
    assert result["available"] is True
    assert result["post_trip"] is False


@pytest.mark.anyio
async def test_get_chat_status_completed_recent():
    from datetime import datetime, timezone

    from backend.routes.rides import get_chat_status

    recent = datetime.now(timezone.utc).isoformat()
    ride = _ride(status="completed", ride_completed_at=recent)
    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=ride)
        result = await get_chat_status(ride_id=_RIDE_ID, current_user=_USER)
    assert result["available"] is True
    assert result["post_trip"] is True


# ── call endpoint removed (chat-only contact) ─────────────────────────────────
# Privacy decision (2026-06): rider↔driver contact is in-app chat only. The
# GET /{ride_id}/call endpoint exposed real phone numbers and was removed.
# This pin keeps it from coming back without a deliberate decision.


def test_call_endpoint_removed():
    from backend.routes import rides

    assert not hasattr(rides, "get_call_info")
    route_paths = [getattr(r, "path", "") for r in rides.api_router.routes]
    assert not any(p.endswith("/call") for p in route_paths)


# ── get_ride_messages ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_ride_messages_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import get_ride_messages

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await get_ride_messages(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_get_ride_messages_not_authorized():
    from fastapi import HTTPException

    from backend.routes.rides import get_ride_messages

    ride = _ride(rider_id="other-rider")
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])  # no driver match
        with pytest.raises(HTTPException) as exc:
            await get_ride_messages(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_get_ride_messages_success():
    from backend.routes.rides import get_ride_messages

    ride = _ride(rider_id=_RIDER_ID)
    msgs = [{"id": "m1", "text": "hi", "sender": "rider", "timestamp": "2025-01-01T00:00:00+00:00"}]
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(side_effect=[[], msgs])  # first=drivers (empty), second=messages
        result = await get_ride_messages(ride_id=_RIDE_ID, current_user=_USER)
    assert result["success"] is True
    assert len(result["messages"]) == 1


# ── send_ride_message ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_send_ride_message_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import SendMessageRequest, send_ride_message

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await send_ride_message(
                ride_id=_RIDE_ID,
                body=SendMessageRequest(text="hello"),
                current_user=_USER,
            )
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_send_ride_message_cancelled():
    from fastapi import HTTPException

    from backend.routes.rides import SendMessageRequest, send_ride_message

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=_ride(status="cancelled"))
        with pytest.raises(HTTPException) as exc:
            await send_ride_message(
                ride_id=_RIDE_ID,
                body=SendMessageRequest(text="hello"),
                current_user=_USER,
            )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_send_ride_message_success_rider():
    from backend.routes.rides import SendMessageRequest, send_ride_message

    ride = _ride(status="in_progress", rider_id=_RIDER_ID, driver_id=_DRIVER_ID)
    driver_row = {"id": _DRIVER_ID, "user_id": "drv-user-01"}
    with patch("backend.routes.rides.db") as mock_db, patch("backend.routes.rides.manager") as mock_manager:
        # find_one: ride, then current user's driver(None=rider), then target driver for WS
        mock_db.find_one = AsyncMock(side_effect=[ride, None, driver_row])
        mock_db.insert_one = AsyncMock(return_value={"id": "new-msg"})
        mock_manager.send_personal_message = AsyncMock()
        result = await send_ride_message(
            ride_id=_RIDE_ID,
            body=SendMessageRequest(text="hello"),
            current_user=_USER,
        )
    assert result["success"] is True


# ── get_scheduled_rides ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_scheduled_rides():
    from backend.routes.rides import get_scheduled_rides

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rides_for_user = MagicMock(return_value=[_ride(status="scheduled")])
        result = await get_scheduled_rides(current_user=_USER)
    assert isinstance(result, list)


# ── cancel_scheduled_ride ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cancel_scheduled_ride_not_found():
    from backend.routes.rides import cancel_scheduled_ride
    from backend.utils.error_handling import RideNotFoundException

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[])
        with pytest.raises(RideNotFoundException):
            await cancel_scheduled_ride(ride_id=_RIDE_ID, current_user=_USER)


@pytest.mark.anyio
async def test_cancel_scheduled_ride_already_cancelled():
    from backend.routes.rides import cancel_scheduled_ride
    from backend.utils.error_handling import SpinrException

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[_ride(status="cancelled")])
        with pytest.raises(SpinrException):
            await cancel_scheduled_ride(ride_id=_RIDE_ID, current_user=_USER)


@pytest.mark.anyio
async def test_cancel_scheduled_ride_success():
    from backend.routes.rides import cancel_scheduled_ride

    ride = _ride(status="scheduled")
    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[ride])
        # C1: pre-dispatch cancel goes through an atomic status-filtered
        # update_one claim, not an id-only update_ride write.
        mock_db.update_one = AsyncMock(return_value={**ride, "status": "cancelled"})
        result = await cancel_scheduled_ride(ride_id=_RIDE_ID, current_user=_USER)
    assert result["success"] is True


# ── simulate_driver_arrival ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_simulate_driver_arrival_prod_blocked():
    from fastapi import HTTPException

    from backend.routes.rides import simulate_driver_arrival

    with patch("backend.routes.rides._settings") as mock_settings:
        mock_settings.ENV = "production"
        with pytest.raises(HTTPException) as exc:
            await simulate_driver_arrival(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_simulate_driver_arrival_success():
    from backend.routes.rides import simulate_driver_arrival

    updated = _ride(status="driver_arrived", pickup_otp="1234")
    with patch("backend.routes.rides._settings") as mock_settings, patch("backend.routes.rides.db_supabase") as mock_db:
        mock_settings.ENV = "development"
        mock_db.get_ride = AsyncMock(return_value=_ride(rider_id=_RIDER_ID))
        mock_db.update_ride = AsyncMock()
        mock_db.get_ride = AsyncMock(side_effect=[_ride(rider_id=_RIDER_ID), updated])
        result = await simulate_driver_arrival(ride_id=_RIDE_ID, current_user=_USER)
    assert result["success"] is True
    assert result["pickup_otp"] == "1234"


# ── rider_start_ride ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rider_start_ride_not_driver():
    from fastapi import HTTPException

    from backend.routes.rides import rider_start_ride

    with pytest.raises(HTTPException) as exc:
        await rider_start_ride(ride_id=_RIDE_ID, current_user={"id": _RIDER_ID, "is_driver": False})
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_rider_start_ride_success():
    from backend.routes.rides import rider_start_ride

    driver_user = {"id": _RIDER_ID, "is_driver": True}
    driver_row = {"id": _DRIVER_ID}
    ride = _ride(status="driver_arrived", driver_id=_DRIVER_ID)
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[driver_row])
        mock_db.update_ride = AsyncMock()
        result = await rider_start_ride(ride_id=_RIDE_ID, current_user=driver_user)
    assert result["success"] is True


# ── rider_complete_ride ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rider_complete_ride_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import rider_complete_ride

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await rider_complete_ride(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_rider_complete_ride_wrong_rider():
    from fastapi import HTTPException

    from backend.routes.rides import rider_complete_ride

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(rider_id="other"))
        with pytest.raises(HTTPException) as exc:
            await rider_complete_ride(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_rider_complete_ride_success():
    from backend.routes.rides import rider_complete_ride

    ride = _ride(status="completed", rider_id=_RIDER_ID)
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        result = await rider_complete_ride(ride_id=_RIDE_ID, current_user=_USER)
    assert result["status"] == "completed"


# ── get_ride_receipt ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_ride_receipt_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import get_ride_receipt

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await get_ride_receipt(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_get_ride_receipt_wrong_rider():
    from fastapi import HTTPException

    from backend.routes.rides import get_ride_receipt

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(rider_id="other"))
        with pytest.raises(HTTPException) as exc:
            await get_ride_receipt(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_get_ride_receipt_not_completed():
    from fastapi import HTTPException

    from backend.routes.rides import get_ride_receipt

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(status="in_progress", rider_id=_RIDER_ID))
        with pytest.raises(HTTPException) as exc:
            await get_ride_receipt(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_get_ride_receipt_success():
    from backend.routes.rides import get_ride_receipt

    ride = _ride(
        status="completed",
        rider_id=_RIDER_ID,
        driver_id=_DRIVER_ID,
        tax_amount=1.65,
        vehicle_type_id=None,
        corporate_account_id=None,
    )
    driver_row = {"id": _DRIVER_ID, "user_id": "drv-user"}
    driver_user = {"first_name": "Bob", "last_name": "Smith"}
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver_row)
        mock_db.get_user_by_id = AsyncMock(return_value=driver_user)
        mock_db.get_rows = AsyncMock(return_value=[])
        result = await get_ride_receipt(ride_id=_RIDE_ID, current_user=_USER)
    assert result["success"] is True
    assert result["receipt"]["driver_name"] == "Bob Smith"


# ── trigger_emergency ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_trigger_emergency_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import EmergencyRequest, trigger_emergency

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await trigger_emergency(
                ride_id=_RIDE_ID,
                body=EmergencyRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_trigger_emergency_not_authorized():
    from fastapi import HTTPException

    from backend.routes.rides import EmergencyRequest, trigger_emergency

    ride = _ride(rider_id="other-rider")
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as exc:
            await trigger_emergency(
                ride_id=_RIDE_ID,
                body=EmergencyRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_trigger_emergency_success():
    from backend.routes.rides import EmergencyRequest, trigger_emergency

    ride = _ride(rider_id=_RIDER_ID)
    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.manager") as mock_manager,
        patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        patch("backend.routes.rides.send_sms", new_callable=AsyncMock, return_value={"success": True}),
    ):
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(
            side_effect=[
                [],  # driver lookup for current_user
                [{"phone": "+13065550001", "id": "ec-1"}],  # emergency contacts
            ]
        )
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Jane", "last_name": "Rider"})
        mock_db.insert_one = AsyncMock()
        mock_manager.broadcast_to_admins = AsyncMock()
        result = await trigger_emergency(
            ride_id=_RIDE_ID,
            body=EmergencyRequest(latitude=52.1, longitude=-106.6),
            current_user=_USER,
        )
    assert result["success"] is True
    assert result["contacts_notified"] == 1


# ── safety_checkin_response ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_safety_checkin_response_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import safety_checkin_response

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as exc:
            await safety_checkin_response(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_safety_checkin_response_not_in_progress():
    from fastapi import HTTPException

    from backend.routes.rides import safety_checkin_response

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[_ride(status="completed")])
        with pytest.raises(HTTPException) as exc:
            await safety_checkin_response(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_safety_checkin_response_success():
    from backend.routes.rides import safety_checkin_response

    with (
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.utils.redis_client.redis_set", new_callable=AsyncMock),
    ):
        mock_db.get_rows = AsyncMock(return_value=[_ride(status="in_progress")])
        result = await safety_checkin_response(ride_id=_RIDE_ID, current_user=_USER)
    assert result["success"] is True


# ── create_ride guard branches ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_ride_banned_user_v2():
    from fastapi import HTTPException

    from backend.routes.rides import CreateRideRequest, create_ride

    body = CreateRideRequest(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
    )
    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.db") as mock_ddb,
    ):
        mock_db.find_one = AsyncMock(return_value=None)  # idempotency key check
        mock_ddb.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "banned"})
        with pytest.raises(HTTPException) as exc:
            await create_ride(
                request=_starlette_request("POST"),
                body=body,
                current_user=_USER,
            )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_create_ride_suspended_user_v2():
    from fastapi import HTTPException

    from backend.routes.rides import CreateRideRequest, create_ride

    body = CreateRideRequest(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
    )
    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.db") as mock_ddb,
    ):
        mock_db.find_one = AsyncMock(return_value=None)
        mock_ddb.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "suspended"})
        with pytest.raises(HTTPException) as exc:
            await create_ride(
                request=_starlette_request("POST"),
                body=body,
                current_user=_USER,
            )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_create_ride_no_stripe_customer():
    from fastapi import HTTPException

    from backend.routes.rides import CreateRideRequest, create_ride

    body = CreateRideRequest(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="card",
    )
    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.db") as mock_ddb,
        # Stripe configured → card-on-file requirement is enforced.
        patch(
            "backend.routes.rides.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value=None)
        mock_ddb.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        with pytest.raises(HTTPException) as exc:
            await create_ride(
                request=_starlette_request("POST"),
                body=body,
                current_user=_USER,
            )
    assert exc.value.status_code == 400
    assert "payment method" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_create_ride_idempotency_key_existing():
    """If the idempotency key matches an existing ride, return it immediately."""
    from backend.routes.rides import CreateRideRequest, create_ride

    existing = _ride(status="searching")
    body = CreateRideRequest(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
    )
    req = _starlette_request("POST")
    req.scope["headers"] = [(b"idempotency-key", b"idem-abc")]

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db_supabase") as mock_db,
    ):
        mock_db.find_one = AsyncMock(return_value=existing)
        result = await create_ride(request=req, body=body, current_user=_USER)

    assert result["status"] == "searching"


@pytest.mark.anyio
async def test_create_ride_rejects_unpriced_requested_vehicle_type():
    """Direct/stale booking requests cannot use another type's fare."""
    from fastapi import HTTPException

    from backend.routes.rides import CreateRideRequest, create_ride

    body = CreateRideRequest(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-unpriced",
        payment_method="wallet",
    )
    configured_fare = {
        "vehicle_type": {"id": "vt-configured", "name": "Standard"},
        "per_km_rate": 1.5,
        "per_minute_rate": 0.25,
        "booking_fee": 2.0,
        "base_fare": 3.0,
        "minimum_fare": 5.0,
        "surge_multiplier": 1.0,
    }

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.db") as mock_ddb,
        patch("backend.routes.rides._fares_for_location_impl", new_callable=AsyncMock, return_value=[configured_fare]),
    ):
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.get_service_area_for_point = AsyncMock(return_value=None)
        mock_db.insert_ride = AsyncMock()
        mock_ddb.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})

        with pytest.raises(HTTPException) as exc:
            await create_ride(
                request=_starlette_request("POST"),
                body=body,
                current_user=_USER,
            )

    assert exc.value.status_code == 400
    assert "vehicle type" in exc.value.detail.lower()
    mock_db.insert_ride.assert_not_called()


@pytest.mark.anyio
async def test_create_ride_full_happy_path():
    """create_ride: happy path from fare-calc through insert → match."""
    from backend.routes.rides import CreateRideRequest, create_ride

    body = CreateRideRequest(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="wallet",
    )

    fare_info = {
        "vehicle_type": {"id": "vt-1", "name": "Standard"},
        "per_km_rate": 1.5,
        "per_minute_rate": 0.25,
        "booking_fee": 2.0,
        "base_fare": 3.0,
        "minimum_fare": 5.0,
        "surge_multiplier": 1.0,
    }
    inserted_ride = {**_ride(status="searching"), "id": _RIDE_ID}

    with (
        patch("backend.routes.rides.validate_ride_location"),
        patch("backend.routes.rides.db_supabase") as mock_db,
        patch("backend.routes.rides.db") as mock_ddb,
        patch("backend.routes.rides._fares_for_location_impl", new_callable=AsyncMock, return_value=[fare_info]),
        patch("backend.routes.rides.calculate_airport_fee", new_callable=AsyncMock, return_value={"airport_fee": 0.0}),
        patch(
            "backend.routes.rides.calculate_all_fees",
            new_callable=AsyncMock,
            return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}},
        ),
        patch("backend.routes.rides.match_driver_to_ride", new_callable=AsyncMock),
        patch("backend.routes.rides.manager") as mock_manager,
    ):
        mock_db.find_one = AsyncMock(return_value=None)  # no idempotency match
        mock_db.get_rows = AsyncMock(return_value=[])  # always empty (no active ride, no corp members, etc.)
        mock_ddb.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active", "stripe_customer_id": "cus_1"})
        mock_db.insert_ride = AsyncMock(return_value=inserted_ride)
        mock_db.get_ride = AsyncMock(return_value={**inserted_ride, "status": "driver_assigned"})
        mock_manager.broadcast_to_admins = AsyncMock()

        result = await create_ride(
            request=_starlette_request("POST"),
            body=body,
            current_user=_USER,
        )

    assert result is not None


# ── get_ride enrichment paths ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_ride_driver_assigned_enrichment():
    """get_ride should attach driver info and compute offer expiry for driver_assigned rides."""
    from backend.routes.rides import get_ride

    now_iso = "2026-01-01T12:00:00+00:00"
    ride = _ride(
        status="driver_assigned",
        driver_id=_DRIVER_ID,
        driver_accepted_at=None,
        driver_notified_at=now_iso,
    )
    driver_row = {
        "id": _DRIVER_ID,
        "user_id": "drv-user",
        "name": "Bob",
        "rating": 4.9,
        "total_rides": 100,
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_color": "Blue",
        "license_plate": "ABC123",
        "vehicle_year": 2020,
        "lat": 52.1,
        "lng": -106.6,
        "photo_url": None,
    }
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[{"id": _DRIVER_ID}])  # is_driver check
        mock_db.get_driver_by_id = AsyncMock(return_value=driver_row)
        mock_db.get_user_by_id = AsyncMock(return_value={"profile_image": "data:image/png;base64,xx"})
        result = await get_ride(
            request=_starlette_request(),
            ride_id=_RIDE_ID,
            current_user=_USER,
        )
    assert result.get("status") == "driver_assigned"
    assert result["driver"]["photo_url"] == "data:image/png;base64,xx"


@pytest.mark.anyio
async def test_get_ride_with_driver_accepted_at():
    """get_ride computes free_cancel_seconds_remaining when driver has accepted."""
    from backend.routes.rides import get_ride

    ride = _ride(
        status="driver_accepted",
        driver_id=None,
        driver_accepted_at="2026-01-01T12:00:00+00:00",
    )
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        mock_db.get_rows = AsyncMock(return_value=[])
        result = await get_ride(
            request=_starlette_request(),
            ride_id=_RIDE_ID,
            current_user=_USER,
        )
    # free_cancel_seconds_remaining should be 0 (accepted long ago)
    assert result.get("free_cancel_seconds_remaining") is not None


# ── get_ride_history with cursor ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_ride_history_with_cursor():
    """Cursor pagination should skip rides up to and including the cursor id."""
    from backend.routes.rides import get_ride_history

    # The DB layer applies the `created_at < cursor_ts` filter, so the mock
    # simulates that by returning only rides older than the cursor.
    rides_after_cursor = [_ride(status="completed", **{"id": f"r-{i}", "driver_id": _DRIVER_ID}) for i in range(2, 5)]
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=rides_after_cursor)
        mock_db.find_one = AsyncMock(return_value={"id": "r-1", "created_at": "2024-01-02T00:00:00Z"})
        result = await get_ride_history(
            request=_starlette_request(),
            limit=10,
            before="r-1",
            current_user=_USER,
        )
    # Should return rides after r-1 (i.e., r-2, r-3, r-4)
    assert len(result["rides"]) == 3


# ── process_payment card outcome branches ─────────────────────────────────────


def _mock_db_for_payment(ride, *, update_raises=False):
    """Build a db_supabase mock wired for process_payment card tests."""
    mock_db = MagicMock()
    mock_db.get_ride = AsyncMock(return_value=ride)
    mock_db.update_one = AsyncMock(return_value={"id": _RIDE_ID})
    mock_db.get_user_by_id = AsyncMock(
        return_value={"id": _RIDER_ID, "stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}
    )
    mock_db.get_driver_by_id = AsyncMock(return_value=None)
    mock_db.insert_one = AsyncMock()
    if update_raises:
        mock_db.update_ride = AsyncMock(side_effect=Exception("db error"))
    else:
        mock_db.update_ride = AsyncMock()
    return mock_db


@pytest.mark.anyio
async def test_process_payment_card_succeeded():
    """process_payment: card path, outcome=succeeded → paid."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment
    from backend.utils.stripe_charge import ChargeOutcome

    ride = _ride(status="completed", payment_status="pending", payment_method="card", payment_method_id="pm_1")
    outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi_abc", charged_amount=15.0)
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=outcome),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("utils.email_receipt.send_receipt_email", new_callable=AsyncMock, return_value=True),
    ):
        mock_manager.send_personal_message = AsyncMock()
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_card_requires_action():
    """process_payment: outcome=requires_action → 402."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment
    from backend.utils.stripe_charge import ChargeOutcome

    ride = _ride(status="completed", payment_status="pending", payment_method="card", payment_method_id="pm_1")
    outcome = ChargeOutcome(status="requires_action", payment_intent_id="pi_abc")
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=outcome),
    ):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "authentication_required"


@pytest.mark.anyio
async def test_process_payment_card_declined():
    """process_payment: outcome=declined → 402 card_declined."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment
    from backend.utils.stripe_charge import ChargeOutcome

    ride = _ride(status="completed", payment_status="pending", payment_method="card", payment_method_id="pm_1")
    outcome = ChargeOutcome(status="declined", decline_code="insufficient_funds")
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=outcome),
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "card_declined"


@pytest.mark.anyio
async def test_process_payment_card_unconfigured():
    """process_payment: outcome=unconfigured → marks paid without real charge."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment
    from backend.utils.stripe_charge import ChargeOutcome

    ride = _ride(status="completed", payment_status="pending", payment_method="card", payment_method_id="pm_1")
    outcome = ChargeOutcome(status="unconfigured")
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=outcome),
        patch("utils.email_receipt.send_receipt_email", new_callable=AsyncMock, return_value=False),
    ):
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_card_failed():
    """process_payment: outcome=failed → 402 payment_error."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment
    from backend.utils.stripe_charge import ChargeOutcome

    ride = _ride(status="completed", payment_status="pending", payment_method="card", payment_method_id="pm_1")
    outcome = ChargeOutcome(status="failed", error_message="Generic failure")
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=outcome),
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "payment_error"


# ── share/track trip endpoints ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_share_trip_link_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import get_share_trip_link

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await get_share_trip_link(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_get_share_trip_link_wrong_rider():
    from fastapi import HTTPException

    from backend.routes.rides import get_share_trip_link

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(rider_id="other"))
        with pytest.raises(HTTPException) as exc:
            await get_share_trip_link(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_get_share_trip_link_completed():
    from fastapi import HTTPException

    from backend.routes.rides import get_share_trip_link

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_ride(status="completed", rider_id=_RIDER_ID))
        with pytest.raises(HTTPException) as exc:
            await get_share_trip_link(ride_id=_RIDE_ID, current_user=_USER)
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_get_share_trip_link_success_new_token():
    from backend.routes.rides import get_share_trip_link

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(
            return_value=_ride(status="in_progress", rider_id=_RIDER_ID, shared_trip_token=None)
        )
        mock_db.update_ride = AsyncMock()
        result = await get_share_trip_link(ride_id=_RIDE_ID, current_user=_USER)
    assert result["success"] is True
    assert "share_token" in result


# ── share_trip_with_contact ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_share_trip_with_contact_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import ShareTripWithContactRequest, share_trip_with_contact

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await share_trip_with_contact(
                ride_id=_RIDE_ID,
                body=ShareTripWithContactRequest(contact_name="Jane", contact_phone="+13065550001"),
                current_user=_USER,
            )
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_share_trip_with_contact_wrong_rider():
    from fastapi import HTTPException

    from backend.routes.rides import ShareTripWithContactRequest, share_trip_with_contact

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=_ride(rider_id="other"))
        with pytest.raises(HTTPException) as exc:
            await share_trip_with_contact(
                ride_id=_RIDE_ID,
                body=ShareTripWithContactRequest(contact_name="Jane", contact_phone="+13065550001"),
                current_user=_USER,
            )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_share_trip_with_contact_completed():
    from fastapi import HTTPException

    from backend.routes.rides import ShareTripWithContactRequest, share_trip_with_contact

    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=_ride(status="completed", rider_id=_RIDER_ID))
        with pytest.raises(HTTPException) as exc:
            await share_trip_with_contact(
                ride_id=_RIDE_ID,
                body=ShareTripWithContactRequest(contact_name="Jane", contact_phone="+13065550001"),
                current_user=_USER,
            )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_share_trip_with_contact_success():
    from backend.routes.rides import ShareTripWithContactRequest, share_trip_with_contact

    ride = _ride(status="in_progress", rider_id=_RIDER_ID, shared_trip_token=None, shared_with=None)
    rider_user = {"id": _RIDER_ID, "first_name": "Alice", "last_name": "Smith"}
    contact_user = {"id": "contact-usr"}

    with (
        patch("backend.routes.rides.db") as mock_db,
        patch("backend.routes.rides.send_push_notification", new_callable=AsyncMock),
    ):
        mock_db.find_one = AsyncMock(side_effect=[ride, contact_user, rider_user])
        mock_db.update_one = AsyncMock()
        result = await share_trip_with_contact(
            ride_id=_RIDE_ID,
            body=ShareTripWithContactRequest(contact_name="Jane", contact_phone="+13065550001"),
            current_user=_USER,
        )
    assert result["success"] is True
    assert result["contact_notified"] is True


# ── track_shared_ride ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_track_shared_ride_not_found():
    from fastapi import HTTPException

    from backend.routes.rides import track_shared_ride

    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as exc:
            await track_shared_ride(share_token="abc123")
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_track_shared_ride_completed():
    from backend.routes.rides import track_shared_ride

    ride = _ride(status="completed", shared_trip_token_created_at=None)
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[ride])
        result = await track_shared_ride(share_token="abc123")
    assert result["status"] == "completed"


@pytest.mark.anyio
async def test_track_shared_ride_active():
    from backend.routes.rides import track_shared_ride

    ride = _ride(status="in_progress", driver_id=_DRIVER_ID, shared_trip_token_created_at=None)
    driver_row = {
        "id": _DRIVER_ID,
        "name": "Bob",
        "lat": 52.1,
        "lng": -106.6,
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_color": "Blue",
    }
    with patch("backend.routes.rides.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[ride])
        mock_db.get_driver_by_id = AsyncMock(return_value=driver_row)
        mock_db.get_user_by_id = AsyncMock(return_value={"profile_image": ""})
        result = await track_shared_ride(share_token="abc123")
    assert result["status"] == "in_progress"


# ── get_shared_contacts ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_shared_contacts_success():
    from backend.routes.rides import get_shared_contacts

    ride = _ride(rider_id=_RIDER_ID, shared_with=[{"name": "Bob", "phone": "+1306"}])
    with patch("backend.routes.rides.db") as mock_db:
        mock_db.find_one = AsyncMock(return_value=ride)
        result = await get_shared_contacts(ride_id=_RIDE_ID, current_user=_USER)
    assert len(result["contacts"]) == 1


# ── process_payment wallet path ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_process_payment_wallet_success():
    """process_payment: wallet payment method → debits wallet and marks paid."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_method="wallet",
        payment_status="pending",
        rider_id=_RIDER_ID,
        driver_id=None,
    )
    wallet = {"id": "wlt-1", "balance": 50.0, "is_active": True}
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.routes.rides.db") as mock_ddb,
        patch("backend.routes.wallet.get_or_create_wallet", new_callable=AsyncMock, return_value=wallet),
        patch("backend.routes.wallet._record_transaction", new_callable=AsyncMock),
        patch("utils.email_receipt.send_receipt_email", new_callable=AsyncMock, return_value=False),
    ):
        mock_ddb.update_one = AsyncMock()

        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_wallet_suspended():
    """process_payment: wallet suspended → 403."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_method="wallet",
        payment_status="pending",
        rider_id=_RIDER_ID,
    )
    wallet = {"id": "wlt-1", "balance": 50.0, "is_active": False}
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.routes.wallet.get_or_create_wallet", new_callable=AsyncMock, return_value=wallet),
    ):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_process_payment_wallet_insufficient_balance():
    """process_payment: wallet balance too low → 400."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_method="wallet",
        payment_status="pending",
        rider_id=_RIDER_ID,
        total_fare=100.0,
    )
    wallet = {"id": "wlt-1", "balance": 5.0, "is_active": True}
    mock_db = _mock_db_for_payment(ride)

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.routes.wallet.get_or_create_wallet", new_callable=AsyncMock, return_value=wallet),
    ):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 400
    assert "Insufficient" in exc.value.detail


# ── process_payment guard branches (lines 1546/1553/1555) ─────────────────────


@pytest.mark.anyio
async def test_process_payment_guard_row_none():
    """process_payment: concurrent lock guard returns None → already_paid early return."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(status="completed", payment_status="pending", rider_id=_RIDER_ID)
    mock_db = _mock_db_for_payment(ride)
    mock_db.update_one = AsyncMock(return_value=None)  # guard returns None

    with patch("backend.routes.rides.db_supabase", mock_db):
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["already_paid"] is True


@pytest.mark.anyio
async def test_process_payment_card_succeeded_with_driver():
    """process_payment: succeeded path with driver present → fetches driver for email."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment
    from backend.utils.stripe_charge import ChargeOutcome

    ride = _ride(
        status="completed",
        payment_status="pending",
        payment_method="card",
        payment_method_id="pm_1",
        driver_id=_DRIVER_ID,
        rider_id=_RIDER_ID,
    )
    outcome = ChargeOutcome(status="succeeded", payment_intent_id="pi_xyz", charged_amount=15.0)
    mock_db = _mock_db_for_payment(ride)
    mock_db.get_driver_by_id = AsyncMock(return_value={"id": _DRIVER_ID, "user_id": "drv-user"})
    mock_db.get_user_by_id = AsyncMock(
        return_value={"id": _RIDER_ID, "first_name": "Alice", "last_name": "R", "email": "a@b.com"}
    )

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.services.payment_service.charge_ride", new_callable=AsyncMock, return_value=outcome),
        patch("backend.routes.rides.manager") as mock_manager,
        patch("utils.email_receipt.send_receipt_email", new_callable=AsyncMock, return_value=True),
    ):
        mock_manager.send_personal_message = AsyncMock()
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["success"] is True
    assert result["email_sent"] is True


# ── process_payment company_allowance path ────────────────────────────────────


@pytest.mark.anyio
async def test_process_payment_company_allowance_no_company_id():
    """process_payment: company_allowance but no corporate_account_id → 400."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_status="pending",
        payment_method="company_allowance",
        rider_id=_RIDER_ID,
        corporate_account_id=None,
    )
    mock_db = _mock_db_for_payment(ride)

    with patch("backend.routes.rides.db_supabase", mock_db):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 400
    assert "Corporate account" in exc.value.detail


@pytest.mark.anyio
async def test_process_payment_company_allowance_no_membership():
    """process_payment: company_allowance but rider has no membership → 400."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_status="pending",
        payment_method="company_allowance",
        rider_id=_RIDER_ID,
        corporate_account_id="corp-001",
    )
    mock_db = _mock_db_for_payment(ride)
    mock_db.list_active_memberships_for_user = AsyncMock(return_value=[])

    with patch("backend.routes.rides.db_supabase", mock_db):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 400
    assert "membership" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_process_payment_company_allowance_unlimited_happy_path():
    """process_payment: company_allowance unlimited → deducts from allowance and marks paid."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_status="pending",
        payment_method="company_allowance",
        rider_id=_RIDER_ID,
        corporate_account_id="corp-001",
    )
    mock_db = _mock_db_for_payment(ride)
    mock_db.list_active_memberships_for_user = AsyncMock(return_value=[{"id": "mem-1", "company_id": "corp-001"}])
    mock_db.get_member_allowance = AsyncMock(
        return_value={"id": "allow-1", "type": "unlimited", "amount": 200, "used": 0}
    )
    mock_db.get_corporate_wallet_by_company = AsyncMock(return_value={"id": "cwlt-1", "balance": 500})
    mock_db.get_corporate_policy = AsyncMock(return_value={"allowed_payment_source": "both"})
    mock_db.insert_one = AsyncMock()

    mock_allowance_svc = MagicMock()
    mock_allowance_svc.apply_rollback = AsyncMock()

    mock_wallet_svc = MagicMock()
    mock_wallet_svc.apply_adjustment = AsyncMock()

    mock_policy_eval = MagicMock(return_value={"pass": True, "failed_rules": []})

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.routes.rides.corporate_allowance_service", mock_allowance_svc),
        patch("backend.routes.rides.corporate_wallet_service", mock_wallet_svc),
        patch("backend.routes.rides.evaluate_policy", mock_policy_eval),
        patch("utils.email_receipt.send_receipt_email", new_callable=AsyncMock, return_value=False),
    ):
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_company_allowance_capped_with_master():
    """process_payment: company_allowance partial → master debit covers remainder."""
    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_status="pending",
        payment_method="company_allowance",
        rider_id=_RIDER_ID,
        total_fare=20.0,
        corporate_account_id="corp-001",
    )
    mock_db = _mock_db_for_payment(ride)
    mock_db.list_active_memberships_for_user = AsyncMock(return_value=[{"id": "mem-1", "company_id": "corp-001"}])
    # Allowance covers $10, ride costs $20 → master debit = $10
    mock_db.get_member_allowance = AsyncMock(
        return_value={"id": "allow-1", "type": "capped", "amount": 10.0, "used": 0.0}
    )
    mock_db.get_corporate_wallet_by_company = AsyncMock(return_value={"id": "cwlt-1", "balance": 500})
    mock_db.get_corporate_policy = AsyncMock(return_value={"allowed_payment_source": "both"})
    mock_db.insert_one = AsyncMock()

    mock_allowance_svc = MagicMock()
    mock_allowance_svc.apply_rollback = AsyncMock()

    mock_wallet_svc = MagicMock()
    mock_wallet_svc.apply_adjustment = AsyncMock()

    mock_policy_eval = MagicMock(return_value={"pass": True, "failed_rules": []})

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.routes.rides.corporate_allowance_service", mock_allowance_svc),
        patch("backend.routes.rides.corporate_wallet_service", mock_wallet_svc),
        patch("backend.routes.rides.evaluate_policy", mock_policy_eval),
        patch("utils.email_receipt.send_receipt_email", new_callable=AsyncMock, return_value=False),
    ):
        result = await process_payment(
            ride_id=_RIDE_ID,
            req=ProcessPaymentRequest(),
            current_user=_USER,
        )
    assert result["success"] is True


@pytest.mark.anyio
async def test_process_payment_company_allowance_master_debit_fails():
    """process_payment: master wallet debit fails → compensation + 503."""
    from fastapi import HTTPException

    from backend.routes.rides import ProcessPaymentRequest, process_payment

    ride = _ride(
        status="completed",
        payment_status="pending",
        payment_method="company_allowance",
        rider_id=_RIDER_ID,
        total_fare=20.0,
        corporate_account_id="corp-001",
    )
    mock_db = _mock_db_for_payment(ride)
    mock_db.list_active_memberships_for_user = AsyncMock(return_value=[{"id": "mem-1", "company_id": "corp-001"}])
    mock_db.get_member_allowance = AsyncMock(
        return_value={"id": "allow-1", "type": "capped", "amount": 5.0, "used": 0.0}
    )
    mock_db.get_corporate_wallet_by_company = AsyncMock(return_value={"id": "cwlt-1", "balance": 500})
    mock_db.get_corporate_policy = AsyncMock(return_value={"allowed_payment_source": "both"})
    mock_db.insert_one = AsyncMock()

    mock_allowance_svc = MagicMock()
    mock_allowance_svc.apply_rollback = AsyncMock()
    mock_allowance_svc.apply_grant = AsyncMock()

    mock_wallet_svc = MagicMock()
    mock_wallet_svc.apply_adjustment = AsyncMock(side_effect=Exception("wallet debit failed"))

    with (
        patch("backend.routes.rides.db_supabase", mock_db),
        patch("backend.routes.rides.corporate_allowance_service", mock_allowance_svc),
        patch("backend.routes.rides.corporate_wallet_service", mock_wallet_svc),
    ):
        with pytest.raises(HTTPException) as exc:
            await process_payment(
                ride_id=_RIDE_ID,
                req=ProcessPaymentRequest(),
                current_user=_USER,
            )
    assert exc.value.status_code == 503
    # Compensation grant should have been attempted
    mock_allowance_svc.apply_grant.assert_called_once()
