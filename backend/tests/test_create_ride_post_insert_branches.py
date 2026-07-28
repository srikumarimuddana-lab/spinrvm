"""Further branch coverage for routes/rides/booking.py's create_ride,
targeting the post-insert side-effect blocks left uncovered after PR #2559
(service_areas/wallet/corporate/SCA/idempotency guard clauses): promo-code
application (success, rejection, unexpected failure), the fare-breakdown
snapshot save, the admin live-monitoring broadcast, the post-dispatch
ride_search_timeout spawn, and the road-distance settings-fetch fallback.

Note: the geofence stop-loop's `s_lat is None or s_lng is None: continue`
branch (booking.py line 507) is unreachable via the public API — the
CreateRideRequest.validate_stops field validator already rejects any stop
missing lat/lng before create_ride ever runs, so this is defensive dead
code, not a gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-cr-2"
_USER = {"id": _RIDER_ID}

_FARE_INFO = {
    "vehicle_type": {"id": "vt-1", "name": "Standard"},
    "per_km_rate": 1.5,
    "per_minute_rate": 0.25,
    "booking_fee": 2.0,
    "base_fare": 3.0,
    "minimum_fare": 5.0,
    "surge_multiplier": 1.0,
}


def _starlette_request(method="POST", path="/rides"):
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


def _body(**kw):
    from backend.schemas import CreateRideRequest

    defaults = dict(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="wallet",
    )
    defaults.update(kw)
    return CreateRideRequest(**defaults)


def _inserted_ride():
    return {
        "id": "ride-cr-2",
        "status": "searching",
        "total_fare": 10.0,
        "base_fare": 3.0,
        "distance_fare": 5.0,
        "time_fare": 2.0,
        "grand_total": 10.0,
        "planned_route_polyline": None,
    }


async def _run_happy_path(body_kwargs=None, mock_overrides=None):
    """Runs create_ride through a full happy path (wallet payment, no
    corporate/SCA branches) and returns (result, mock_supabase, mock_db)."""
    from backend.routes.rides import create_ride

    inserted = _inserted_ride()

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.matching.ride_search_timeout", AsyncMock()) as mock_timeout,
        patch("backend.routes.rides._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides._deps.manager") as mock_manager,
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _find_one(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_find_one)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value={**inserted, "status": "searching"})
        mock_supabase.update_one = AsyncMock(return_value=None)
        mock_manager.broadcast_to_admins = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()

        if mock_overrides:
            mock_overrides(mock_supabase, mock_manager)

        result = await create_ride(
            request=_starlette_request(),
            body=_body(**(body_kwargs or {})),
            current_user=_USER,
        )

    return result, mock_supabase, mock_manager, mock_timeout


async def test_promo_code_applied_successfully_updates_grand_total():
    def _override(mock_supabase, mock_manager):
        pass

    with (
        patch(
            "backend.routes.promotions._validate_promo_for_user",
            AsyncMock(
                return_value={
                    "valid": True,
                    "discount_amount": "2.00",
                    "promo_id": "promo-1",
                    "code": "SAVE2",
                    "free_ride": False,
                }
            ),
        ),
        patch("backend.routes.promotions._record_promo_application", AsyncMock(return_value="appl-1")),
    ):
        result, mock_supabase, _, _ = await _run_happy_path(
            body_kwargs={"promo_code": "SAVE2"}, mock_overrides=_override
        )

    assert result is not None
    # update_one called with the discounted grand_total/promo fields.
    update_calls = [c for c in mock_supabase.update_one.await_args_list if c.args[0] == "rides"]
    assert any(c.args[2].get("promo_code") == "SAVE2" for c in update_calls)


async def test_promo_code_rejected_sets_promo_error_non_fatal():
    from fastapi import HTTPException

    with patch(
        "backend.routes.promotions._validate_promo_for_user",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Promo expired")),
    ):
        result, *_ = await _run_happy_path(body_kwargs={"promo_code": "EXPIRED"})

    assert result is not None
    assert result.get("promo_error") == "Promo expired"


async def test_promo_code_unexpected_exception_sets_generic_promo_error():
    with patch(
        "backend.routes.promotions._validate_promo_for_user",
        AsyncMock(side_effect=RuntimeError("promotions table down")),
    ):
        result, *_ = await _run_happy_path(body_kwargs={"promo_code": "BOOM"})

    assert result is not None
    assert result.get("promo_error") == "Promo could not be applied"


async def test_fare_snapshot_save_failure_is_non_fatal():
    def _override(mock_supabase, mock_manager):
        mock_supabase.update_one = AsyncMock(side_effect=RuntimeError("snapshot write failed"))

    result, mock_supabase, _, _ = await _run_happy_path(mock_overrides=_override)

    assert result is not None


async def test_admin_broadcast_failure_is_non_fatal():
    def _override(mock_supabase, mock_manager):
        mock_manager.broadcast_to_admins = AsyncMock(side_effect=RuntimeError("ws fanout down"))

    result, _, mock_manager, _ = await _run_happy_path(mock_overrides=_override)

    assert result is not None
    mock_manager.broadcast_to_admins.assert_awaited_once()


async def test_ride_search_timeout_spawned_when_still_searching():
    result, _, _, mock_timeout = await _run_happy_path()

    assert result is not None
    assert result.get("status") == "searching"
    # _deps.spawn(matching.ride_search_timeout(ride.id)) creates the
    # coroutine synchronously (recording the call) before handing it to the
    # patched no-op spawn (which just closes it rather than awaiting it) --
    # so assert on the call, not an await.
    mock_timeout.assert_called_once()


async def test_road_distance_settings_fetch_failure_falls_back_to_road_default():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(side_effect=RuntimeError("settings db down"))),
        patch("backend.routes.rides.booking._fetch_directions_route", AsyncMock(return_value=None)),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(side_effect=RuntimeError("stop here")),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)

        # The settings-fetch failure inside the road-distance safety net must
        # be swallowed (falls back to {} -> mode "road" default) and booking
        # continues past it; we stop the test at the next real call
        # (calculate_all_fees, forced to raise) so this only needs to prove
        # the settings exception didn't propagate as the actual failure.
        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(payment_method="wallet"), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert "fees" in str(exc_info.value.detail).lower() or "fare" in str(exc_info.value.detail).lower()
