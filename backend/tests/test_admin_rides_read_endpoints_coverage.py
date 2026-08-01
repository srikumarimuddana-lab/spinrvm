"""Coverage-closing unit tests for the remaining read/list/export/analytics
endpoints of backend/routes/admin/rides.py (A1b Track 1 #4, continued pass).

`tests/test_admin_rides_coverage.py` already covers the mutation/money-adjacent
endpoints in this file (cancel, complete, create, send-invoice, payout retry/
bulk-retry/close-period) plus a first smoke pass over the simpler read
endpoints. This file adds the endpoints that pass had zero coverage on:
dashboard stats, ride location-trail/live/invoice, send-receipt, the Google
Places/fare-estimate/promo-preview proxies, the route-map.png Static Maps
proxy, heatmap data, earnings (+ /rides + /overview), the CSV-style exports
(/export/rides, /export/drivers), and /payouts/overview.

Same "lighter smoke pass" convention as the prior file for this class of
endpoint: happy path + one DB/upstream-exception path per endpoint, not every
branch — a bug here degrades a dashboard/export view rather than corrupting
production ride or money state. Follows the existing `client` +
`_set_super_admin(app_fixture)`-equivalent (`as_super_admin`) fixture pattern
established in test_admin_rides_coverage.py / test_admin_business_logic.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SUPER_ADMIN = {
    "id": "admin-001",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "rides", "earnings", "support"],
}


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def as_super_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


def _http_ctx(status_code=200, json_data=None, content=b"", raise_for_status_error=False):
    """Async-context-manager mock for `async with httpx.AsyncClient() as client:`."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = str(json_data) if json_data is not None else ""
    if json_data is not None:
        resp.json.return_value = json_data
    if raise_for_status_error:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError("err", request=MagicMock(), response=resp)
    else:
        resp.raise_for_status.return_value = None

    inner_client = MagicMock()
    inner_client.get = AsyncMock(return_value=resp)
    inner_client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=inner_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# GET /stats — dashboard tiles
# ---------------------------------------------------------------------------


class TestAdminDashboardStats:
    def test_stats_happy_path(self, client, as_super_admin):
        rollup = [
            {"sum_total_fare": "10.00", "sum_driver_earnings": "8.00", "sum_admin_earnings": "2.00", "sum_tip": "1.00"}
        ]
        with (
            patch("db_supabase.count_documents", AsyncMock(return_value=3)),
            patch("db_supabase.rpc", AsyncMock(return_value=rollup)),
        ):
            resp = client.get("/api/admin/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_rides"] == 3
        assert body["revenue_today"] == 10.0


# ---------------------------------------------------------------------------
# GET /rides/{id}/location-trail, /live, /invoice
# ---------------------------------------------------------------------------


class TestAdminRideLocationTrail:
    def test_location_trail_happy_path(self, client, as_super_admin):
        trail = [{"lat": 50.4, "lng": -104.6, "tracking_phase": "trip_in_progress"}]
        with patch("db_supabase.get_ride_location_trail", AsyncMock(return_value=trail)):
            resp = client.get("/api/admin/rides/ride-1/location-trail")
        assert resp.status_code == 200
        assert resp.json() == trail


class TestAdminLiveRide:
    def test_live_ride_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_live_ride_data", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/live")
        assert resp.status_code == 404

    def test_live_ride_happy_path(self, client, as_super_admin):
        data = {"id": "ride-1", "status": "in_progress", "driver_lat": 50.4, "driver_lng": -104.6}
        with patch("db_supabase.get_live_ride_data", AsyncMock(return_value=data)):
            resp = client.get("/api/admin/rides/ride-1/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"


class TestAdminRideInvoice:
    def test_invoice_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/invoice")
        assert resp.status_code == 404

    def test_invoice_happy_path_no_snapshot(self, client, as_super_admin):
        ride = {"id": "ride-1", "status": "completed", "total_fare": "12.50", "grand_total": "12.50"}
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)):
            resp = client.get("/api/admin/rides/ride-1/invoice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["fare_locked"] is False
        assert body["grand_total"] == "12.50"

    def test_invoice_fare_locked_snapshot_used(self, client, as_super_admin):
        ride = {
            "id": "ride-1",
            "status": "completed",
            "total_fare": "12.50",
            "fare_breakdown_snapshot": {"lines": [{"label": "Base", "amount": "5.00"}], "grand_total": "12.50"},
        }
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"fare_lock_enabled": True})),
        ):
            resp = client.get("/api/admin/rides/ride-1/invoice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["fare_locked"] is True
        assert body["fare_breakdown"] == [{"label": "Base", "amount": "5.00"}]


# ---------------------------------------------------------------------------
# POST /rides/{id}/send-receipt
# ---------------------------------------------------------------------------


class TestAdminSendReceipt:
    _RIDE = {"id": "ride-1", "rider_id": "usr-1", "tip_amount": "1.00"}

    def test_send_receipt_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride", AsyncMock(return_value=None)):
            resp = client.post("/api/admin/rides/no-ride/send-receipt")
        assert resp.status_code == 404

    def test_send_receipt_no_rider_email_422(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=self._RIDE)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": ""})),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 422

    def test_send_receipt_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=self._RIDE)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": "r@example.com"})),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=True), create=True),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 200
        assert resp.json()["sent"] is True

    def test_send_receipt_provider_failure_502(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=self._RIDE)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": "r@example.com"})),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=False), create=True),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 502

    def test_send_receipt_override_email_accepted(self, client, as_super_admin):
        """No rider on file at all, but an override email in the body is enough."""
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value={**self._RIDE, "rider_id": None})),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=True), create=True),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt", json={"email": "override@example.com"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /places/autocomplete, /places/details
# ---------------------------------------------------------------------------


class TestAdminPlacesAutocomplete:
    def test_autocomplete_no_api_key_503(self, client, as_super_admin):
        with patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})):
            resp = client.get("/api/admin/places/autocomplete", params={"input": "Main St"})
        assert resp.status_code == 503

    def test_autocomplete_happy_path(self, client, as_super_admin):
        google_resp = {"suggestions": []}
        with (
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            patch("httpx.AsyncClient", return_value=_http_ctx(json_data=google_resp)),
            patch("routes.admin.rides.legacy_predictions_from_new_response", return_value=[]),
        ):
            resp = client.get("/api/admin/places/autocomplete", params={"input": "Main St"})
        assert resp.status_code == 200
        assert resp.json() == {"predictions": []}

    def test_autocomplete_upstream_error_502(self, client, as_super_admin):
        with (
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            patch("httpx.AsyncClient", return_value=_http_ctx(status_code=500, raise_for_status_error=True)),
        ):
            resp = client.get("/api/admin/places/autocomplete", params={"input": "Main St"})
        assert resp.status_code == 502


class TestAdminPlacesDetails:
    def test_details_no_api_key_503(self, client, as_super_admin):
        with patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})):
            resp = client.get("/api/admin/places/details", params={"place_id": "abc"})
        assert resp.status_code == 503

    def test_details_happy_path(self, client, as_super_admin):
        with (
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            patch("httpx.AsyncClient", return_value=_http_ctx(json_data={"id": "abc"})),
            patch("routes.admin.rides.legacy_details_from_new_response", return_value={"place_id": "abc"}),
        ):
            resp = client.get("/api/admin/places/details", params={"place_id": "abc"})
        assert resp.status_code == 200
        assert resp.json() == {"place_id": "abc"}

    def test_details_transport_error_502(self, client, as_super_admin):
        with (
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            patch("httpx.AsyncClient", side_effect=RuntimeError("network down")),
        ):
            resp = client.get("/api/admin/places/details", params={"place_id": "abc"})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /rides/fare-estimate, POST /promo/preview
# ---------------------------------------------------------------------------


class TestAdminFareEstimate:
    def test_fare_estimate_happy_path(self, client, as_super_admin):
        estimate = {"base_fare": 3.5, "grand_total": 12.5}
        with patch("features.fare_estimate", AsyncMock(return_value=estimate), create=True):
            resp = client.get(
                "/api/admin/rides/fare-estimate",
                params={
                    "pickup_lat": 50.4,
                    "pickup_lng": -104.6,
                    "dropoff_lat": 50.45,
                    "dropoff_lng": -104.5,
                    "distance_km": 5.2,
                    "duration_minutes": 12,
                    "vehicle_type_id": "vt-1",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["grand_total"] == 12.5


class TestAdminPromoPreview:
    def test_promo_preview_happy_path(self, client, as_super_admin):
        validation = {
            "code": "SAVE5",
            "discount_type": "fixed",
            "discount_amount": "5.00",
            "promo_id": "promo-1",
            "description": "Save $5",
        }
        with patch("routes.promotions._validate_promo_for_user", AsyncMock(return_value=validation), create=True):
            resp = client.post(
                "/api/admin/promo/preview",
                json={"rider_id": "usr-1", "code": "SAVE5", "ride_fare": "20.00"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["code"] == "SAVE5"


# ---------------------------------------------------------------------------
# GET /rides/{id}/route-map.png
# ---------------------------------------------------------------------------


class TestAdminRouteMapPng:
    def test_route_map_ride_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/route-map.png")
        assert resp.status_code == 404

    def test_route_map_missing_coordinates_400(self, client, as_super_admin):
        ride = {"id": "ride-1", "pickup_lat": None, "dropoff_lat": None}
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)):
            resp = client.get("/api/admin/rides/ride-1/route-map.png")
        assert resp.status_code == 400

    def test_route_map_uses_prerendered_snapshot(self, client, as_super_admin):
        ride = {"id": "ride-1", "route_snapshot_url": "https://example.com/snap.png"}
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("httpx.AsyncClient", return_value=_http_ctx(status_code=200, content=b"PNGDATA")),
        ):
            resp = client.get("/api/admin/rides/ride-1/route-map.png")
        assert resp.status_code == 200
        assert resp.content == b"PNGDATA"

    def test_route_map_no_api_key_falls_through_to_503(self, client, as_super_admin):
        ride = {
            "id": "ride-1",
            "pickup_lat": 50.4,
            "pickup_lng": -104.6,
            "dropoff_lat": 50.45,
            "dropoff_lng": -104.5,
            "location_trail": [],
        }
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})),
        ):
            resp = client.get("/api/admin/rides/ride-1/route-map.png")
        assert resp.status_code == 503

    def test_route_map_google_static_maps_happy_path(self, client, as_super_admin):
        ride = {
            "id": "ride-1",
            "pickup_lat": 50.4,
            "pickup_lng": -104.6,
            "dropoff_lat": 50.45,
            "dropoff_lng": -104.5,
            "location_trail": [
                {"lat": 50.41, "lng": -104.59, "tracking_phase": "trip_in_progress"},
            ],
        }
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            patch("httpx.AsyncClient", return_value=_http_ctx(status_code=200, content=b"PNGDATA")),
        ):
            resp = client.get("/api/admin/rides/ride-1/route-map.png")
        assert resp.status_code == 200

    def test_route_map_upstream_non_200_returns_502(self, client, as_super_admin):
        ride = {
            "id": "ride-1",
            "pickup_lat": 50.4,
            "pickup_lng": -104.6,
            "dropoff_lat": 50.45,
            "dropoff_lng": -104.5,
            "location_trail": [],
        }
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            patch("httpx.AsyncClient", return_value=_http_ctx(status_code=500, content=b"error")),
        ):
            resp = client.get("/api/admin/rides/ride-1/route-map.png")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /rides/heatmap-data
# ---------------------------------------------------------------------------


class TestAdminHeatmapData:
    def test_heatmap_happy_path(self, client, as_super_admin):
        rows = [
            {
                "pickup_lat": 50.4,
                "pickup_lng": -104.6,
                "dropoff_lat": 50.45,
                "dropoff_lng": -104.5,
                "corporate_account_id": None,
            },
            {
                "pickup_lat": 50.5,
                "pickup_lng": -104.7,
                "dropoff_lat": None,
                "dropoff_lng": None,
                "corporate_account_id": "corp-1",
            },
        ]
        with patch("db_supabase.get_rows", AsyncMock(return_value=rows)):
            resp = client.get("/api/admin/rides/heatmap-data")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["total_rides"] == 2
        assert body["stats"]["corporate_rides"] == 1
        assert len(body["pickup_points"]) == 2
        assert len(body["dropoff_points"]) == 1

    def test_heatmap_with_filters(self, client, as_super_admin):
        with patch("db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = client.get(
                "/api/admin/rides/heatmap-data",
                params={
                    "filter": "corporate",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-31",
                    "service_area_id": "area-1",
                    "group_by": "pickup",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["stats"]["total_rides"] == 0


# ---------------------------------------------------------------------------
# GET /earnings, /earnings/rides, /earnings/overview
# ---------------------------------------------------------------------------


class TestAdminEarnings:
    def test_earnings_happy_path_month(self, client, as_super_admin):
        rollup = [
            {
                "sum_total_fare": "100.00",
                "completed_count": 10,
                "sum_driver_earnings": "80.00",
                "sum_admin_earnings": "20.00",
            }
        ]
        with patch("db_supabase.rpc", AsyncMock(return_value=rollup)):
            resp = client.get("/api/admin/earnings", params={"period": "month"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_revenue"] == 100.0
        assert body["total_rides"] == 10

    def test_earnings_day_period(self, client, as_super_admin):
        with patch("db_supabase.rpc", AsyncMock(return_value=[])):
            resp = client.get("/api/admin/earnings", params={"period": "day"})
        assert resp.status_code == 200
        assert resp.json()["period"] == "day"


class TestAdminEarningsRides:
    def test_earnings_rides_happy_path(self, client, as_super_admin):
        rides = [
            {
                "id": "ride-1",
                "ride_code": "R1",
                "status": "completed",
                "total_fare": "12.50",
                "driver_earnings": "10.00",
                "admin_earnings": "2.50",
                "tip_amount": "1.00",
                "tax_amount": "0.60",
                "discount_amount": "0",
                "surge_multiplier": "1.0",
                "stripe_charge_id": "ch_1",
                "driver_id": "drv-1",
                "rider_id": "usr-1",
                "service_area_id": "area-1",
                "ride_completed_at": "2026-07-01T00:00:00Z",
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get("/api/admin/earnings/rides")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["rides"][0]["ride_id"] == "ride-1"

    def test_earnings_rides_empty(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get(
                "/api/admin/earnings/rides", params={"start_date": "2026-01-01", "end_date": "2026-01-31"}
            )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestAdminEarningsOverview:
    _AGG_ROW = [
        {
            "gbv": "1000.00",
            "platform": "150.00",
            "trips": 50,
            "riders": 30,
            "drivers": 10,
            "surge_revenue": "20.00",
            "promo_spend": "10.00",
            "promo_count": 2,
            "gst_collected": "50.00",
            "pst_collected": "60.00",
            "cx_count": 5,
            "cx_revenue": "0",
            "cx_rider_cancels": 3,
            "cx_driver_cancels": 1,
            "fn_price_searches": 100,
            "fn_requested": 80,
            "fn_reached_searching": 70,
            "fn_completed": 50,
            "fn_cancelled_after_start": 1,
        }
    ]
    _REF_ROW = [{"refund_amount": "5.00", "refund_count": 1}]

    def test_earnings_overview_happy_path(self, client, as_super_admin):
        async def _rpc(name, params=None, **kwargs):
            if name == "admin_earnings_overview_agg":
                return self._AGG_ROW
            if name == "admin_earnings_refunds":
                return self._REF_ROW
            if name == "admin_earnings_daily_series":
                return []
            return []

        with (
            patch("db_supabase.rpc", AsyncMock(side_effect=_rpc)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/earnings/overview", params={"period": "7d"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["metrics"]["gbv"]["current"] == 1000.0
        assert body["metrics"]["completed_trips"]["current"] == 50

    def test_earnings_overview_mtd_with_service_area(self, client, as_super_admin):
        async def _rpc(name, params=None, **kwargs):
            return []

        with (
            patch("db_supabase.rpc", AsyncMock(side_effect=_rpc)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/earnings/overview", params={"period": "mtd", "service_area_id": "area-1"})
        assert resp.status_code == 200
        assert resp.json()["period"]["key"] == "mtd"


# ---------------------------------------------------------------------------
# GET /export/rides, /export/drivers
# ---------------------------------------------------------------------------


class TestAdminExportRidesFiltered:
    def test_export_rides_happy_path(self, client, as_super_admin):
        rides = [
            {
                "id": "ride-1",
                "pickup_address": "A",
                "dropoff_address": "B",
                "total_fare": "10.00",
                "status": "completed",
                "created_at": "2026-07-01T00:00:00Z",
                "rider_id": None,
                "driver_id": None,
            }
        ]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/admin/export/rides")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["rides"][0]["fare"] == "10.00"

    def test_export_rides_writes_audit_log(self, client, as_super_admin):
        captured: dict = {}

        async def _insert(table, doc):
            captured["table"] = table
            captured["doc"] = doc

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
            patch("db_supabase.insert_one", AsyncMock(side_effect=_insert)),
        ):
            resp = client.get("/api/admin/export/rides")
        assert resp.status_code == 200
        assert captured["table"] == "audit_logs"
        assert captured["doc"]["action"] == "export_rides"


class TestAdminExportDrivers:
    def test_export_drivers_happy_path_no_drivers(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/admin/export/drivers")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_export_drivers_with_one_driver(self, client, as_super_admin):
        driver = {
            "id": "drv-1",
            "driver_code": "D001",
            "user_id": "usr-1",
            "status": "active",
            "is_verified": True,
            "is_online": False,
            "is_available": False,
            "vehicle_type_id": "vt-1",
            "service_area_id": "area-1",
            "license_number": None,
            "vehicle_vin": "1HGCM82633A123456",
            "created_at": "2026-01-01T00:00:00Z",
        }

        async def _get_rows(table, *args, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "users":
                return [
                    {"id": "usr-1", "first_name": "Dan", "last_name": "D", "email": "d@example.com", "phone": "555"}
                ]
            if table == "vehicle_types":
                return [{"id": "vt-1", "name": "Sedan"}]
            if table == "service_areas":
                return [{"id": "area-1", "name": "Regina"}]
            if table == "driver_subscriptions":
                return []
            return []

        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/admin/export/drivers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        row = body["drivers"][0]
        assert row["id"] == "drv-1"
        assert row["vehicle_type"] == "Sedan"
        # VIN must be masked to last-4, never exported in full (PIPEDA).
        assert row["vehicle_vin"] == "*" * 13 + "3456"
        assert row["license_no"] is None


# ---------------------------------------------------------------------------
# GET /payouts/overview
# ---------------------------------------------------------------------------


class TestAdminPayoutsOverview:
    def test_payouts_overview_happy_path(self, client, as_super_admin):
        agg = [
            {
                "earned_up_to_end": "1000.00",
                "paid_up_to_end": "800.00",
                "earned_up_to_prev": "900.00",
                "paid_up_to_prev": "700.00",
                "stuck_count": 0,
                "stuck_amount": "0",
                "blocked_count": 0,
                "blocked_outstanding": "0",
                "t4a_under_500": 5,
                "t4a_500_10k": 2,
                "t4a_10k_30k": 1,
                "t4a_over_30k": 0,
                "t4a_drivers_with_earnings": 8,
                "t4a_ytd_gross": "5000.00",
            }
        ]
        with (
            patch("db_supabase.rpc", AsyncMock(return_value=agg)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/payouts/overview", params={"period": "7d"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["metrics"]["outstanding_payable"]["current"] == 200.0
        assert body["t4a_snapshot"]["drivers_with_earnings"] == 8

    def test_payouts_overview_service_area_no_drivers_returns_empty_shell(self, client, as_super_admin):
        with patch("db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = client.get("/api/admin/payouts/overview", params={"service_area_id": "area-empty"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["metrics"]["total_paid_out"]["current"] == 0.0
        assert body["daily_series"] == []

    def test_payouts_overview_period_locks_query_failure_degrades_gracefully(self, client, as_super_admin):
        """period_locks lookup failing must not break the whole dashboard (it's wrapped
        in its own try/except per CLAUDE.md's read-endpoint degrade-gracefully pattern)."""
        agg = [
            {
                "earned_up_to_end": "0",
                "paid_up_to_end": "0",
                "earned_up_to_prev": "0",
                "paid_up_to_prev": "0",
                "stuck_count": 0,
                "stuck_amount": "0",
                "blocked_count": 0,
                "blocked_outstanding": "0",
                "t4a_under_500": 0,
                "t4a_500_10k": 0,
                "t4a_10k_30k": 0,
                "t4a_over_30k": 0,
                "t4a_drivers_with_earnings": 0,
                "t4a_ytd_gross": "0",
            }
        ]

        async def _get_rows(table, *args, **kwargs):
            if table == "payouts":
                return []
            if table == "audit_logs":
                raise RuntimeError("db down")
            return []

        with (
            patch("db_supabase.rpc", AsyncMock(return_value=agg)),
            patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        ):
            resp = client.get("/api/admin/payouts/overview")
        assert resp.status_code == 200
        assert resp.json()["period_locks"] == []
