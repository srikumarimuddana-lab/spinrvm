"""Targeted branch coverage for routes/rides/estimates.py.

Covers guard clauses and fail-open paths that test_coverage_rides.py's
happy-path estimate_ride tests don't reach: service-area geofence rejections
(pickup/dropoff/stop), malformed-driver-row skip reasons, the cascade
vehicle-type fallback, the price-search analytics tracker, and the
Directions route fetch/await fail-open paths. The ~475-line core fare loop
in compute_ride_estimates (per-vehicle fare_breakdown assembly) is exercised
end-to-end by the existing happy-path tests and is not re-covered here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.routes.rides.estimates import (
    RideEstimateRequest,
    _track_price_search,
    compute_ride_estimates,
)

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-est-1"

_FARES = [
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


def _body(**kw):
    defaults = dict(pickup_lat=52.1, pickup_lng=-106.6, dropoff_lat=52.2, dropoff_lng=-106.7)
    defaults.update(kw)
    return RideEstimateRequest(**defaults)


def _active_area(**kw):
    base = {"id": "area-1", "name": "Zone A", "is_active": True}
    base.update(kw)
    return base


# ── geofence guards ─────────────────────────────────────────────────────────


async def test_rejects_pickup_outside_service_area():
    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._get_active_service_area_for_point", AsyncMock(return_value=None)),
    ):
        mock_db.get_rows = AsyncMock(return_value=[_active_area()])

        with pytest.raises(Exception) as exc_info:
            await compute_ride_estimates(_body(), _RIDER_ID)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "OUTSIDE_SERVICE_AREA"


async def test_rejects_dropoff_outside_service_area():
    pickup_area = _active_area()

    async def _area_for_point(lat, lng, areas):
        # Pickup matches; dropoff does not.
        return pickup_area if (lat, lng) == (52.1, -106.6) else None

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch(
            "backend.routes.rides.estimates._get_active_service_area_for_point",
            AsyncMock(side_effect=_area_for_point),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[pickup_area])

        with pytest.raises(Exception) as exc_info:
            await compute_ride_estimates(_body(), _RIDER_ID)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "OUTSIDE_SERVICE_AREA"


async def test_stop_outside_service_area_rejected_and_missing_coords_skipped():
    pickup_area = _active_area()

    async def _area_for_point(lat, lng, areas):
        # Pickup and dropoff both match; the one real stop does not.
        if (lat, lng) in ((52.1, -106.6), (52.2, -106.7)):
            return pickup_area
        return None

    stops = [
        {"lat": None, "lng": None},  # missing coords → continue (line 236-238)
        {"lat": 52.15, "lng": -106.65},  # outside service area → reject
    ]

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch(
            "backend.routes.rides.estimates._get_active_service_area_for_point",
            AsyncMock(side_effect=_area_for_point),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[pickup_area])

        with pytest.raises(Exception) as exc_info:
            await compute_ride_estimates(_body(stops=stops), _RIDER_ID)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "OUTSIDE_SERVICE_AREA"


# ── malformed driver rows (skip_reasons) ────────────────────────────────────


async def test_skips_malformed_driver_rows_and_counts_valid_one():
    drivers = [
        {"user_id": None, "lat": 52.1, "lng": -106.6, "vehicle_type_id": "vt-1"},  # no_user_id
        {"user_id": "u-2", "lat": None, "lng": None, "vehicle_type_id": "vt-1"},  # no_lat_lng
        {"user_id": "u-3", "lat": 60.0, "lng": -106.6, "vehicle_type_id": "vt-1"},  # outside_10km
        {"user_id": "u-4", "lat": 52.1, "lng": -106.6, "vehicle_type_id": None},  # no_vehicle_type_id
        {"user_id": "u-5", "lat": 52.1, "lng": -106.6, "vehicle_type_id": "vt-1"},  # valid
    ]

    def _calc_distance(lat1, lng1, lat2, lng2):
        return 999.0 if (lat2, lng2) == (60.0, -106.6) else 1.0

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._deps.calculate_distance", side_effect=_calc_distance),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch("backend.routes.rides.estimates._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
    ):
        mock_db.get_rows = AsyncMock(side_effect=[[], drivers])
        mock_db.get_service_area_for_point = AsyncMock(return_value=None)

        result = await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=False)

    assert result["estimates"][0]["driver_count"] == 1


# ── cascade vehicle-type fallback ───────────────────────────────────────────


async def test_parent_cascade_map_fetch_failure_is_non_fatal():
    matched_area = _active_area(vehicle_cascade_map=None, parent_service_area_id="parent-1")

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch(
            "backend.routes.rides.estimates._get_active_service_area_for_point",
            AsyncMock(return_value=matched_area),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch("backend.routes.rides.estimates._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
    ):
        mock_db.get_rows = AsyncMock(side_effect=[[matched_area], []])
        mock_db.find_one = AsyncMock(side_effect=RuntimeError("db down"))

        result = await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=False)

    assert result["estimates"][0]["available"] is False


async def test_cascade_fallback_marks_vehicle_available_via_upgrade_type():
    matched_area = _active_area(vehicle_cascade_map=[{"from": "vt-1", "to": ["vt-2"]}])
    cascade_driver = {"user_id": "u-1", "lat": 52.1, "lng": -106.6, "vehicle_type_id": "vt-2"}

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch(
            "backend.routes.rides.estimates._get_active_service_area_for_point",
            AsyncMock(return_value=matched_area),
        ),
        patch("backend.routes.rides.estimates._deps.calculate_distance", return_value=1.0),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch("backend.routes.rides.estimates._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
    ):
        mock_db.get_rows = AsyncMock(side_effect=[[matched_area], [cascade_driver]])

        result = await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=False)

    assert result["estimates"][0]["available"] is True
    assert result["estimates"][0]["driver_count"] == 1


# ── calculate_all_fees failure surfaces 503 ─────────────────────────────────


async def test_calculate_all_fees_failure_raises_503():
    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._get_active_service_area_for_point", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(side_effect=RuntimeError("fees db down")),
        ),
        patch("backend.routes.rides.estimates._deps.get_app_settings", AsyncMock(return_value={})),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])

        with pytest.raises(Exception) as exc_info:
            await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=False)

    assert exc_info.value.status_code == 503


# ── Directions route fetch/await fail-open paths ────────────────────────────


async def test_route_fetch_exception_is_non_fatal_and_falls_back_to_haversine():
    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._get_active_service_area_for_point", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch(
            "backend.routes.rides.estimates._deps.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "key-123", "fare_distance_basis": "road"}),
        ),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
        patch("backend.routes.rides.estimates._deps._metric_observe"),
        patch(
            "backend.routes.rides.estimates._fetch_directions_route",
            AsyncMock(side_effect=RuntimeError("maps down")),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])

        result = await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=True)

    assert result["route_polyline"] is None
    assert result["estimates"][0]["distance_basis"] in ("haversine", "haversine_fallback")


async def test_final_polyline_await_timeout_is_non_fatal():
    import asyncio

    async def _never_finishes():
        await asyncio.sleep(10)
        return {"polyline": "abc", "distance_km": 5.0}

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._get_active_service_area_for_point", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch(
            "backend.routes.rides.estimates._deps.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "key-123", "fare_distance_basis": "haversine"}),
        ),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
        patch("backend.routes.rides.estimates._deps._metric_observe"),
        patch(
            "backend.routes.rides.estimates._fetch_directions_route",
            _never_finishes,
        ),
        patch("backend.routes.rides.estimates._deps.spawn", side_effect=lambda coro: asyncio.ensure_future(coro)),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])

        result = await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=True)

    assert result["route_polyline"] is None


# ── B6: Directions latency measurement ──────────────────────────────────────


async def test_directions_latency_is_recorded_on_success():
    """B6: spinr_fare_directions_duration_ms must observe every real
    Directions call so the timeout constants (DIRECTIONS_TIMEOUT_S /
    _PRICING_ROUTE_WAIT_S) can eventually be re-tuned from data instead of
    judgement."""
    observed = []

    def _capture(name, value, *_args, **_kwargs):
        if name == "spinr_fare_directions_duration_ms":
            observed.append(value)

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._get_active_service_area_for_point", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch(
            "backend.routes.rides.estimates._deps.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "key-123", "fare_distance_basis": "road"}),
        ),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
        patch("backend.routes.rides.estimates._deps._metric_observe", side_effect=_capture),
        patch(
            "backend.routes.rides.estimates._fetch_directions_route",
            AsyncMock(return_value={"polyline": [[52.1, -106.6]], "distance_km": 5.0, "duration_s": 600}),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=True)

    assert len(observed) == 1
    assert observed[0] >= 0.0


async def test_directions_latency_is_recorded_even_on_failure():
    """A slow/failed Directions call is still latency the SLA dashboards
    must see (same fail-open-but-observe convention as utils/metrics.time_ms) —
    must not be skipped just because the route fetch itself fails-open to
    haversine."""
    observed = []

    def _capture(name, value, *_args, **_kwargs):
        if name == "spinr_fare_directions_duration_ms":
            observed.append(value)

    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.estimates._get_active_service_area_for_point", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch(
            "backend.routes.rides.estimates._deps.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "key-123", "fare_distance_basis": "road"}),
        ),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
        patch("backend.routes.rides.estimates._deps._metric_observe", side_effect=_capture),
        patch(
            "backend.routes.rides.estimates._fetch_directions_route",
            AsyncMock(side_effect=RuntimeError("maps down")),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=True)

    assert len(observed) == 1


# ── _track_price_search ──────────────────────────────────────────────────────


async def test_track_price_search_inserts_row():
    with patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db:
        mock_db.insert_one = AsyncMock(return_value=None)
        await _track_price_search(_RIDER_ID, "area-1")

    mock_db.insert_one.assert_awaited_once_with(
        "price_searches", {"user_id": _RIDER_ID, "service_area_id": "area-1"}
    )


async def test_track_price_search_failure_is_swallowed():
    with patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db:
        mock_db.insert_one = AsyncMock(side_effect=RuntimeError("insert failed"))
        # Must not raise — fire-and-forget analytics write.
        await _track_price_search(_RIDER_ID, "area-1")


async def test_compute_ride_estimates_triggers_price_search_tracking():
    with (
        patch("backend.routes.rides.estimates._deps.validate_ride_location"),
        patch("backend.routes.rides.estimates._deps.get_fares_for_location", AsyncMock(return_value=_FARES)),
        patch("backend.routes.rides.estimates._deps.db_supabase") as mock_db,
        patch(
            "backend.routes.rides.estimates._get_active_service_area_for_point",
            AsyncMock(return_value=_active_area()),
        ),
        patch("backend.routes.rides.estimates._deps.calculate_distance", return_value=1.0),
        patch("backend.routes.rides.estimates._fetch_directions_route", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.estimates._deps.calculate_airport_fee",
            AsyncMock(return_value={"airport_fee": 0.0}),
        ),
        patch(
            "backend.routes.rides.estimates._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides.estimates._deps.sign_estimate_token", return_value="tok"),
        patch("backend.routes.rides.estimates._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.estimates._deps._metric_inc"),
        patch("backend.routes.rides.estimates._deps._metric_observe"),
    ):
        import asyncio as _asyncio

        spawned = []

        def _spawn(coro):
            task = _asyncio.ensure_future(coro)
            spawned.append(task)
            return task

        with patch("backend.routes.rides.estimates._deps.spawn", side_effect=_spawn):
            mock_db.get_rows = AsyncMock(return_value=[])

            await compute_ride_estimates(_body(), _RIDER_ID, include_polyline=False, track_search=True)

    # One spawn for the road-distance route fetch, one for price-search tracking.
    assert len(spawned) == 2
    assert any("_track_price_search" in repr(t) for t in spawned)
