"""
Tests that PII is stripped from ride responses returned to riders.

The driver row contains sensitive fields (license_number, vehicle_vin,
insurance_expiry_date, etc.) that must NOT leak to riders via the
GET /rides/{id} endpoint.
"""

from unittest.mock import AsyncMock, patch

import pytest


# Full driver row as it would come from the database — includes every
# sensitive field that the PII filter must strip.
FULL_DRIVER_ROW = {
    "id": "driver_1",
    "user_id": "user_driver_1",
    "name": "Jane Driver",
    "phone": "+15551234567",
    "rating": 4.9,
    "total_rides": 150,
    "profile_image_url": "https://example.com/photo.jpg",
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
    "id", "name", "rating", "total_rides", "profile_image_url",
    "vehicle_make", "vehicle_model", "vehicle_color", "license_plate",
    "vehicle_year", "lat", "lng",
}

# Fields that must NEVER appear in the response.
FORBIDDEN_FIELDS = {
    "license_number", "vehicle_vin", "insurance_expiry_date",
    "background_check_expiry_date", "work_eligibility_expiry_date",
    "stripe_account_id", "fcm_token", "phone", "user_id",
    "bank_account", "is_available", "is_online", "is_verified",
    "needs_review", "status",
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
        """The response's `driver` object must NOT contain forbidden fields."""
        with (
            patch("backend.routes.rides.db") as mock_db,
            patch("backend.routes.rides.get_current_user", new_callable=AsyncMock) as mock_auth,
            patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        ):
            mock_auth.return_value = {"id": "user_rider_1", "role": "rider"}
            mock_db.rides.find_one = AsyncMock(return_value=ride_with_driver)
            mock_db.drivers.find_one = AsyncMock(return_value=FULL_DRIVER_ROW)

            from backend.routes.rides import get_ride

            # Build a minimal mock request for the Depends chain
            response = await get_ride("ride_1", current_user={"id": "user_rider_1", "role": "rider"})

            driver_in_response = response.get("driver", {})
            for field in FORBIDDEN_FIELDS:
                assert field not in driver_in_response, (
                    f"PII field '{field}' leaked to rider in GET /rides/{{id}} response"
                )

    @pytest.mark.asyncio
    async def test_allowed_fields_present(self, ride_with_driver):
        """The response's `driver` object contains every allowed field."""
        with (
            patch("backend.routes.rides.db") as mock_db,
            patch("backend.routes.rides.get_current_user", new_callable=AsyncMock),
            patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        ):
            mock_db.rides.find_one = AsyncMock(return_value=ride_with_driver)
            mock_db.drivers.find_one = AsyncMock(return_value=FULL_DRIVER_ROW)

            from backend.routes.rides import get_ride

            response = await get_ride("ride_1", current_user={"id": "user_rider_1", "role": "rider"})

            driver_in_response = response.get("driver", {})
            for field in ALLOWED_FIELDS:
                assert field in driver_in_response, (
                    f"Allowed field '{field}' missing from rider's driver view"
                )

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
            patch("backend.routes.rides.db") as mock_db,
            patch("backend.routes.rides.get_current_user", new_callable=AsyncMock),
            patch("backend.routes.rides.get_app_settings", new_callable=AsyncMock, return_value={}),
        ):
            mock_db.rides.find_one = AsyncMock(return_value=ride_no_driver)
            mock_db.drivers.find_one = AsyncMock(return_value=None)

            from backend.routes.rides import get_ride

            response = await get_ride("ride_2", current_user={"id": "user_rider_1", "role": "rider"})

            assert "driver" not in response or response.get("driver") is None
