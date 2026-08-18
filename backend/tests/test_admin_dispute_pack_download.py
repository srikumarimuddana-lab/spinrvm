"""C23 item 4: GET /api/admin/rides/{ride_id}/dispute-pack (routes/admin/
dispute_pack_download.py's admin_get_dispute_evidence_pack -- its own
router, gated by require_module("support") rather than rides_router's
"rides", per the security-auditor finding on the first version of this
endpoint). Mirrors test_admin_rides_read_endpoints_coverage.py's fixture
pattern (client / as_super_admin)."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

_SUPER_ADMIN = {
    "id": "admin-001",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "rides", "earnings", "support"],
}

_RIDE = {
    "id": "ride-1",
    "ride_code": "SPN-1",
    "rider_id": "rider-1",
    "created_at": "2026-01-01T10:00:00+00:00",
    "ride_completed_at": "2026-01-01T10:30:00+00:00",
    "base_fare": 500,
    "distance_fare": 300,
    "time_fare": 100,
    "booking_fee": 50,
    "airport_fee": 0,
    "tip_amount": 200,
    "total_fare": 1150,
    "offers": [],
    "location_trail": [],
    "driver_code": "DR-1",
}

_DISPUTE_ROW = {
    "id": "sd-1",
    "stripe_dispute_id": "dp_1",
    "ride_id": "ride-1",
    "amount_cents": 1150,
    "reason": "fraudulent",
    "status": "needs_response",
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


def _no_op_admin_log():
    return AsyncMock(return_value=None)


class TestDisputeEvidencePack:
    def test_ride_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/dispute-pack")
        assert resp.status_code == 404

    def test_dispute_lookup_db_error_surfaces_503(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
            patch("db_supabase.get_rows", AsyncMock(side_effect=Exception("db down"))),
        ):
            resp = client.get("/api/admin/rides/ride-1/dispute-pack")
        assert resp.status_code == 503

    def test_happy_path_returns_zip_with_expected_members(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_DISPUTE_ROW])),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})),
            patch("routes.admin.dispute_pack_download.log_admin_action", _no_op_admin_log()),
        ):
            resp = client.get("/api/admin/rides/ride-1/dispute-pack")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert 'filename="dispute_evidence_SPN-1.zip"' in resp.headers["content-disposition"]

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(zf.namelist())
        assert "invoice.pdf" in names
        assert "timeline_and_account_history.pdf" in names
        assert "gps_trail.csv" in names
        assert "cover_letter_DRAFT.txt" in names
        # No Google Maps key configured in this test -> route map omitted,
        # placeholder present instead (best-effort, not a hard failure).
        assert "route_map_UNAVAILABLE.txt" in names
        assert zf.read("invoice.pdf").startswith(b"%PDF")

    def test_no_dispute_row_still_generates_ride_only_pack(self, client, as_super_admin):
        """A support agent may want to pull the pack before a dispute
        officially lands -- must not 404/500 just because stripe_disputes
        has no row yet."""
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})),
            patch("routes.admin.dispute_pack_download.log_admin_action", _no_op_admin_log()),
        ):
            resp = client.get("/api/admin/rides/ride-1/dispute-pack")
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "invoice.pdf" in zf.namelist()

    def test_route_map_failure_does_not_block_pack(self, client, as_super_admin):
        """A missing Google Maps key raises inside _fetch_route_map_png_bytes
        -- the pack must still download with a placeholder, not 503 the
        whole endpoint."""
        ride_with_coords = {
            **_RIDE,
            "pickup_lat": 50.4,
            "pickup_lng": -104.6,
            "dropoff_lat": 50.45,
            "dropoff_lng": -104.5,
        }
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride_with_coords)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_DISPUTE_ROW])),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})),  # no API key
            patch("routes.admin.dispute_pack_download.log_admin_action", _no_op_admin_log()),
        ):
            resp = client.get("/api/admin/rides/ride-1/dispute-pack")
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "route_map_UNAVAILABLE.txt" in zf.namelist()
        assert "route_map.png" not in zf.namelist()

    def test_authz_denied_without_admin(self, client):
        resp = client.get("/api/admin/rides/ride-1/dispute-pack")
        assert resp.status_code in (401, 403)
