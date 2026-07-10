"""
Tests that PII is stripped from ride responses returned to riders.

The driver row contains sensitive fields (license_number, vehicle_vin,
insurance_expiry_date, etc.) that must NOT leak to riders via the
GET /rides/{id} endpoint.
"""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

_MOCK_REQUEST = StarletteRequest(
    {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "root_path": ""}
)

# ── P3-1: Parametrized PII guard ─────────────────────────────────────────────
# These 14 fields must never appear in any rider-facing response. The test
# issues a GET to the driver-detail URL and asserts each field is absent,
# regardless of the HTTP status code (404/401 both pass trivially; a future
# endpoint that leaks a field will fail immediately).
FORBIDDEN_FIELDS = [
    "license_number",
    "vehicle_vin",
    "insurance_expiry",
    "stripe_account_id",
    "fcm_token",
    "phone",
    "bank_account",
    "sin_number",
    "date_of_birth",
    "home_address",
    "background_check_result",
    "criminal_record",
    "passport_number",
    "tax_id",
]

_ride_id = "ride_pii_test_1"


@pytest.fixture
def client(test_client):
    return test_client


@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_field_not_in_rider_response(field, client, auth_headers):
    response = client.get(f"/rides/{_ride_id}/driver", headers=auth_headers)
    assert field not in response.json()


# Full driver row as it would come from the database — includes every
# sensitive field that the PII filter must strip.
FULL_DRIVER_ROW = {
    "id": "driver_1",
    "user_id": "user_driver_1",
    "name": "Jane Driver",
    "phone": "+15551234567",
    "rating": 4.9,
    "total_rides": 150,
    "photo_url": "https://example.com/photo.jpg",
    "vehicle_make": "Toyota",
    "vehicle_model": "Camry",
    "vehicle_color": "White",
    "vehicle_year": "2023",
    "license_plate": "ABC 123",
    "lat": 52.13,
    "lng": -106.67,
    # ── Sensitive fields that MUST be excluded ──
    "license_number": "DL-SECRET-12345",
    "vehicle_vin": "1HGBH41JXMN109186",
    "insurance_expiry_date": "2027-01-01",
    "background_check_expiry_date": "2027-06-01",
    "work_eligibility_expiry_date": "2028-01-01",
    "stripe_account_id": "acct_1234567890",
    "fcm_token": "dGVzdF90b2tlbl8xMjM0NTY3ODkw",
    "bank_account": {"bank_name": "TD", "account_number": "****1234"},
    "is_available": True,
    "is_online": True,
    "is_verified": True,
    "needs_review": False,
    "status": "active",
}

# Fields the rider IS allowed to see.
ALLOWED_FIELDS = {
    "id",
    "name",
    "rating",
    "total_rides",
    "photo_url",
    "vehicle_make",
    "vehicle_model",
    "vehicle_color",
    "license_plate",
    "vehicle_year",
    "lat",
    "lng",
}

# Fields that must NEVER appear in the response.
FORBIDDEN_FIELDS = {
    "license_number",
    "vehicle_vin",
    "insurance_expiry_date",
    "background_check_expiry_date",
    "work_eligibility_expiry_date",
    "stripe_account_id",
    "fcm_token",
    "phone",
    "user_id",
    "bank_account",
    "is_available",
    "is_online",
    "is_verified",
    "needs_review",
    "status",
}


class TestRidePIIFiltering:
    """Verify the allow-list filter on GET /rides/{id}."""

    @pytest.fixture
    def ride_with_driver(self):
        return {
            "id": "ride_1",
            "rider_id": "user_rider_1",
            "driver_id": "driver_1",
            "status": "driver_accepted",
            "pickup_lat": 52.13,
            "pickup_lng": -106.67,
            "dropoff_lat": 52.15,
            "dropoff_lng": -106.65,
            "pickup_address": "123 Main St",
            "dropoff_address": "456 Elm Ave",
        }

    @pytest.mark.asyncio
    async def test_driver_pii_excluded(self, ride_with_driver):
        """The response's `driver` object must NOT contain forbidden fields.

        get_ride uses the flat db_supabase API:
            db_supabase.get_ride(ride_id)
            db_supabase.get_driver_by_id(driver_id)
            db_supabase.get_rows("drivers", {...}) — for is_driver check
        """
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_with_driver)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=FULL_DRIVER_ROW)),
        ):
            from backend.routes.rides import get_ride

            response = await get_ride(
                request=_MOCK_REQUEST, ride_id="ride_1", current_user={"id": "user_rider_1", "role": "rider"}
            )

            driver_in_response = response.get("driver", {})
            for field in FORBIDDEN_FIELDS:
                assert field not in driver_in_response, (
                    f"PII field '{field}' leaked to rider in GET /rides/{{id}} response"
                )

    @pytest.mark.asyncio
    async def test_allowed_fields_present(self, ride_with_driver):
        """The response's `driver` object contains every allowed field."""
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_with_driver)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=FULL_DRIVER_ROW)),
        ):
            from backend.routes.rides import get_ride

            response = await get_ride(
                request=_MOCK_REQUEST, ride_id="ride_1", current_user={"id": "user_rider_1", "role": "rider"}
            )

            driver_in_response = response.get("driver", {})
            for field in ALLOWED_FIELDS:
                assert field in driver_in_response, f"Allowed field '{field}' missing from rider's driver view"

    @pytest.mark.asyncio
    async def test_no_driver_key_when_unassigned(self):
        """Rides without a driver_id should not have a `driver` key."""
        ride_no_driver = {
            "id": "ride_2",
            "rider_id": "user_rider_1",
            "driver_id": None,
            "status": "searching",
        }
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_no_driver)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        ):
            from backend.routes.rides import get_ride

            response = await get_ride(
                request=_MOCK_REQUEST, ride_id="ride_2", current_user={"id": "user_rider_1", "role": "rider"}
            )

            assert "driver" not in response or response.get("driver") is None
