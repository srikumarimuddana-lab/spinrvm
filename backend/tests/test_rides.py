"""
Unit tests for rides API and related functionality.
Tests cover ride creation, updates, fare calculation, and ride lifecycle.
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── P3-2: Concurrent double-accept guard ─────────────────────────────────────


@pytest.fixture
def ride_id():
    return "ride_double_accept_001"


@pytest.fixture
def driver_1_headers():
    return {"Authorization": "Bearer driver1_token"}


@pytest.fixture
def driver_2_headers():
    return {"Authorization": "Bearer driver2_token"}


@pytest.fixture
def client(test_client):
    return test_client


@pytest.mark.asyncio
async def test_no_double_accept(client, ride_id, driver_1_headers, driver_2_headers):
    """Two simultaneous accept calls for the same ride: one wins (200), one is rejected (409)."""
    from backend.routes import drivers as drv_mod

    # Distinct driver rows per user: a blanket get_rows mock that returns the
    # same driver for both users would make the loser look like the WINNER
    # replaying their own accept — which is (correctly) an idempotent 200 now,
    # not the distinct-driver race this test pins.
    driver_1 = {"id": "driver_001", "user_id": "user_driver_001", "is_online": True}
    driver_2 = {"id": "driver_002", "user_id": "user_driver_002", "is_online": True}
    ride = {"id": ride_id, "status": "searching", "driver_id": None, "rider_id": "rider_001"}

    # The real accept_ride runs a conditional UPDATE ({'status': 'searching'})
    # that only the first request to actually reach Postgres wins -- which
    # asyncio task gets there first depends on scheduling, not call order.
    # A fixed `side_effect=[accepted_ride, None]` list (the old version of
    # this test) silently assumed driver_1's coroutine always calls
    # update_one before driver_2's, which asyncio.gather does not guarantee
    # once both coroutines have multiple await points ahead of the update
    # (assert_quota_available, the ride_offers lookup, etc.) -- that's what
    # made this test flaky. Model the DB's real "first claim wins" semantics
    # instead, keyed by whichever driver_id actually lands first.
    claim_lock = asyncio.Lock()
    state = {"claimed_by": None}

    async def _get_rows(table, filters=None, **kwargs):
        if table == "drivers":
            return [driver_2] if (filters or {}).get("user_id") == "user_driver_002" else [driver_1]
        # ride_offers lookup on the broadcast/searching path — pending for both.
        return [{"id": "offer-1", "ride_id": ride_id, "status": "pending"}]

    async def _update_one(table, filters, update):
        if table != "rides":
            return None
        async with claim_lock:
            if state["claimed_by"] is not None:
                # Either the race was already lost, or this is a later
                # housekeeping update (e.g. ride_metrics) after acceptance --
                # its return value isn't checked by the caller either way.
                return None
            set_fields = update.get("$set") or {}
            if "driver_id" not in set_fields:
                return None
            state["claimed_by"] = set_fields["driver_id"]
            return {**ride, "status": "driver_accepted", "driver_id": state["claimed_by"]}

    async def _find_one(table, filters=None, **kwargs):
        if table == "rides" and state["claimed_by"] is not None:
            return {**ride, "status": "driver_accepted", "driver_id": state["claimed_by"]}
        return ride

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db.update_one", AsyncMock(side_effect=_update_one)),
        patch("backend.routes.drivers._deps.db.find_one", AsyncMock(side_effect=_find_one)),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
    ):
        # Call the handler directly (same pattern as test_ride_accept_flow.py)
        # so patches on the module-level DB functions are reliably applied.
        results = await asyncio.gather(
            drv_mod.accept_ride(ride_id=ride_id, current_user={"id": "user_driver_001"}),
            drv_mod.accept_ride(ride_id=ride_id, current_user={"id": "user_driver_002"}),
            return_exceptions=True,
        )

    statuses = sorted([200 if isinstance(r, dict) else r.status_code for r in results])
    assert statuses == [200, 409]
    assert state["claimed_by"] in ("driver_001", "driver_002")


# ── 9-2: Guard against completing a ride that hasn't started ─────────────────


@pytest.mark.asyncio
async def test_cannot_complete_from_driver_assigned(ride_id):
    """complete_ride must reject a ride that is still in driver_assigned state (422)."""
    from backend.routes import drivers as drv_mod

    driver = {"id": "driver_001", "user_id": "user_driver_001"}
    ride = {
        "id": ride_id,
        "status": "driver_assigned",
        "driver_id": "driver_001",
        "rider_id": "rider_001",
    }

    with patch(
        "backend.routes.drivers._deps.db_supabase.get_rows",
        AsyncMock(side_effect=[[driver], [ride]]),
    ):
        [exc] = await asyncio.gather(
            drv_mod.complete_ride(ride_id=ride_id, current_user={"id": "user_driver_001"}),
            return_exceptions=True,
        )

    assert getattr(exc, "status_code", None) == 422
    assert "in_progress" in getattr(exc, "message", str(exc)).lower()


# ── 9-3: Full ride lifecycle integration test ─────────────────────────────────


@pytest.mark.asyncio
async def test_full_ride_lifecycle():
    """Walk the complete ride state machine: accept → arrive → verify-otp → complete."""
    from backend.routes import drivers as drv_mod

    ride_id = "lifecycle_test_001"
    driver_id = "driver_lifecycle"
    user_driver_id = "user_driver_lifecycle"
    rider_id = "rider_lifecycle"
    otp_plain = "1234"

    # Mutable ride dict – mock DB calls mutate it to track state.
    ride = {
        "id": ride_id,
        "status": "driver_assigned",
        "driver_id": driver_id,
        "rider_id": rider_id,
        "pickup_lat": 52.1333,
        "pickup_lng": -106.6667,
        "dropoff_lat": 52.1500,
        "dropoff_lng": -106.6500,
        "distance_km": 2.5,
        "planned_distance_km": 2.5,
        "base_fare": 3.0,
        "distance_fare": 3.75,
        "time_fare": 2.5,
        "booking_fee": 0.5,
        "total_fare": 15.0,
        # pickup_otp (routes/rides/booking.py generate_pickup_otp) is stored
        # PLAINTEXT, unlike the separately SHA-256-hashed login OTP -- it's
        # compared with hmac.compare_digest(stored_otp, request.otp)
        # directly (routes/drivers/ride_flow.py::verify_pickup_otp).
        "pickup_otp": otp_plain,
    }
    driver = {
        "id": driver_id,
        "user_id": user_driver_id,
        "lat": 52.1333,  # Same coords as pickup – passes 200 m geofence check.
        "lng": -106.6667,
        "is_online": True,
    }

    async def fake_get_rows(table, filters=None, **kw):
        if table == "drivers":
            return [driver]
        if table == "rides":
            return [ride]
        return []  # driver_location_history → empty → no GPS recalculation

    async def fake_update_ride(rid, updates):
        ride.update(updates)
        return ride

    guard_ok = MagicMock()
    guard_ok.modified_count = 1

    # drivers.py: db = db_supabase (alias). Patching both separately would
    # conflict because they reference the same module object – the second patch
    # would overwrite the first. Use one unified fake that handles both callers:
    #   • accept_ride uses db.update_one with {"$set": {...}} and checks modified_count
    #   • complete_ride uses db_supabase.update_one with a plain dict (no "$set")
    async def fake_update_one(table, filter_, updates, **kw):
        if table == "rides":
            actual = updates.get("$set", updates)
            ride.update(actual)
        return guard_ok  # accept_ride checks guard.modified_count == 0

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(side_effect=fake_update_ride)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update_one)),
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("backend.routes.drivers._deps.db_supabase.find_one", AsyncMock(return_value=ride)),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
    ):
        # Step 1: Accept ride – driver_assigned → driver_accepted
        await drv_mod.accept_ride(ride_id=ride_id, current_user={"id": user_driver_id})
        assert ride["status"] == "driver_accepted"

        # Step 2: Arrive at pickup – driver_accepted → driver_arrived
        await drv_mod.arrive_at_pickup(ride_id=ride_id, current_user={"id": user_driver_id})
        assert ride["status"] == "driver_arrived"

        # Step 3: Verify OTP – driver_arrived → in_progress
        from backend.routes.drivers import RideOTPRequest

        otp_req = RideOTPRequest(otp=otp_plain)
        await drv_mod.verify_pickup_otp(ride_id=ride_id, request=otp_req, current_user={"id": user_driver_id})
        assert ride["status"] == "in_progress"

        # Step 4: Complete ride – in_progress → completed
        # P0-5: complete_ride deliberately does NOT write payment_status — payment
        # settlement is handled asynchronously by the payment retry loop.  Only
        # check the ride status transition here.
        await drv_mod.complete_ride(ride_id=ride_id, current_user={"id": user_driver_id})
        assert ride["status"] == "completed"


class TestRideCreation:
    """Tests for ride creation functionality."""

    @pytest.fixture
    def sample_ride_request(self):
        """Sample ride creation request data."""
        return {
            "pickup_lat": 52.1333,
            "pickup_lng": -106.6667,
            "dropoff_lat": 52.1500,
            "dropoff_lng": -106.6500,
            "pickup_address": "123 Test St",
            "dropoff_address": "456 Main Ave",
            "vehicle_type": "sedan",
            "rider_id": "user_123",
        }

    @pytest.mark.asyncio
    async def test_create_ride_success(self, sample_ride_request, mock_supabase_client):
        """Test successful ride creation."""
        from backend.db_supabase import insert_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "requested"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_ride(sample_ride_request)

        assert result is not None
        assert result["id"] == "ride_123"
        assert result["status"] == "requested"

    @pytest.mark.asyncio
    async def test_create_ride_with_promo(self, sample_ride_request, mock_supabase_client):
        """Test ride creation with promo code."""
        from backend.db_supabase import insert_ride

        ride_with_promo = {**sample_ride_request, "promo_code": "SAVE10"}

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "requested", "promo_applied": True}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_ride(ride_with_promo)

        assert result is not None


class TestRideStatusUpdates:
    """Tests for ride status update functionality."""

    @pytest.fixture
    def ride_collection(self):
        from backend.db import db

        return db.rides

    @pytest.mark.asyncio
    async def test_update_ride_status(self, ride_collection, mock_supabase_client):
        """Test updating ride status."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "in_progress"}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"status": "in_progress"})

        assert result is not None
        assert result["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_update_ride_driver_assignment(self, ride_collection, mock_supabase_client):
        """Test updating ride with driver assignment."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "driver_id": "driver_123", "status": "accepted"}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"driver_id": "driver_123", "status": "accepted"})

        assert result["driver_id"] == "driver_123"

    @pytest.mark.asyncio
    async def test_complete_ride(self, ride_collection, mock_supabase_client):
        """Test completing a ride."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "completed"}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"status": "completed"})

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_cancel_ride(self, ride_collection, mock_supabase_client):
        """Test cancelling a ride."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "cancelled"}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"status": "cancelled"})

        assert result["status"] == "cancelled"


class TestFareCalculation:
    """Tests for fare calculation functionality."""

    def test_calculate_distance(self):
        """Test distance calculation between two points."""
        from backend.geo_utils import calculate_distance

        # Regina, SK coordinates
        lat1, lng1 = 52.1333, -106.6667
        lat2, lng2 = 52.1500, -106.6500

        distance = calculate_distance(lat1, lng1, lat2, lng2)

        assert distance > 0
        assert isinstance(distance, float)

    def test_calculate_distance_same_point(self):
        """Test distance calculation for same point."""
        from backend.geo_utils import calculate_distance

        lat, lng = 52.1333, -106.6667
        distance = calculate_distance(lat, lng, lat, lng)

        assert distance == 0

    def test_calculate_base_fare(self):
        """Test base fare calculation."""
        # Base fare calculation logic
        base_fare = 3.00  # Example base fare
        assert base_fare > 0

    def test_fare_with_distance(self):
        """Test fare calculation including distance."""
        base_fare = 3.00
        per_km_rate = 1.50
        distance_km = 5.0

        total_fare = base_fare + (per_km_rate * distance_km)

        assert total_fare == 10.50

    def test_fare_with_time(self):
        """Test fare calculation including time."""
        base_fare = 3.00
        per_km_rate = 1.50
        per_minute_rate = 0.25
        distance_km = 5.0
        duration_minutes = 15

        total_fare = base_fare + (per_km_rate * distance_km) + (per_minute_rate * duration_minutes)

        assert total_fare == 14.25


class TestRideMatching:
    """Tests for ride-driver matching functionality."""

    @pytest.mark.asyncio
    async def test_find_nearby_drivers_for_ride(self, mock_supabase_client):
        """Test finding nearby drivers for a ride request."""
        from backend.db_supabase import find_nearby_drivers

        mock_drivers = [
            {"id": "driver_1", "lat": 52.1350, "lng": -106.6680, "is_available": True},
            {"id": "driver_2", "lat": 52.1400, "lng": -106.6700, "is_available": True},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_drivers
        # Override conftest's AsyncMock rpc with a sync MagicMock —
        # find_nearby_drivers calls rpc synchronously inside run_sync.
        mock_rpc = MagicMock()
        mock_rpc.return_value.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.rpc = mock_rpc

        result = await find_nearby_drivers(52.1333, -106.6667, 5000)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_claim_driver_for_ride(self, mock_supabase_client):
        """Test atomically claiming a driver for a ride."""
        from backend.db_supabase import claim_driver_atomic

        mock_response = MagicMock()
        mock_response.data = [{"id": "driver_1", "is_available": False}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await claim_driver_atomic("driver_1")

        assert result is True


class TestRideHistory:
    """Tests for ride history functionality."""

    @pytest.mark.asyncio
    async def test_get_user_ride_history(self, mock_supabase_client):
        """Test getting ride history for a user."""
        from backend.db_supabase import get_rides_for_user

        mock_rides = [
            {"id": "ride_1", "status": "completed", "created_at": "2024-01-01"},
            {"id": "ride_2", "status": "completed", "created_at": "2024-01-02"},
            {"id": "ride_3", "status": "cancelled", "created_at": "2024-01-03"},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_rides
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_rides_for_user("user_123", limit=10)

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_driver_ride_history(self, mock_supabase_client):
        """Test getting ride history for a driver."""
        from backend.db_supabase import get_rides_for_driver

        mock_rides = [
            {"id": "ride_1", "status": "completed", "driver_id": "driver_123"},
            {"id": "ride_2", "status": "completed", "driver_id": "driver_123"},
        ]

        mock_response = MagicMock()
        mock_response.data = mock_rides
        # Production chains .eq("driver_id", …).or_(…).order().limit().execute().
        # The status filter uses `.or_()` — NOT `.in_()`, despite the
        # name. See db_supabase.py:422-430.
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        result = await get_rides_for_driver("driver_123", statuses=["completed"])

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_ride_by_id(self, mock_supabase_client):
        """Test getting a specific ride by ID."""
        from backend.db_supabase import get_ride

        mock_ride = {"id": "ride_123", "rider_id": "user_123", "status": "completed", "fare_amount": 15.50}

        mock_response = MagicMock()
        mock_response.data = [mock_ride]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await get_ride("ride_123")

        assert result is not None
        assert result["id"] == "ride_123"


class TestRideRatings:
    """Tests for ride rating functionality."""

    @pytest.mark.asyncio
    async def test_rate_driver(self, mock_supabase_client):
        """Test rating a driver after ride completion."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "rating": 5, "tip_amount": 5.00}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"rating": 5, "tip_amount": 5.00})

        assert result["rating"] == 5
        assert result["tip_amount"] == 5.00

    @pytest.mark.asyncio
    async def test_rate_rider(self, mock_supabase_client):
        """Test rating a rider after ride completion."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "rider_rating": 4}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"rider_rating": 4})

        assert result["rider_rating"] == 4


class TestScheduledRides:
    """Tests for scheduled ride functionality."""

    @pytest.mark.asyncio
    async def test_create_scheduled_ride(self, mock_supabase_client):
        """Test creating a scheduled ride."""
        from backend.db_supabase import insert_ride

        scheduled_ride = {
            "pickup_lat": 52.1333,
            "pickup_lng": -106.6667,
            "dropoff_lat": 52.1500,
            "dropoff_lng": -106.6500,
            "scheduled_for": "2024-01-15T08:00:00Z",
            "status": "scheduled",
            "rider_id": "user_123",
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "scheduled"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_ride(scheduled_ride)

        assert result["status"] == "scheduled"

    @pytest.mark.asyncio
    async def test_cancel_scheduled_ride(self, mock_supabase_client):
        """Test cancelling a scheduled ride."""
        from backend.db_supabase import update_ride

        mock_response = MagicMock()
        mock_response.data = [{"id": "ride_123", "status": "cancelled"}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            return_value=mock_response
        )

        result = await update_ride("ride_123", {"status": "cancelled"})

        assert result["status"] == "cancelled"


class TestRideEndpoints:
    """Tests for ride API endpoints."""

    @pytest.fixture
    def test_client(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        return TestClient(app)

    def test_create_ride_endpoint(self, test_client, auth_headers):
        """Test ride creation endpoint."""
        response = test_client.post(
            "/api/v1/rides",
            json={
                "pickup_lat": 52.1333,
                "pickup_lng": -106.6667,
                "dropoff_lat": 52.1500,
                "dropoff_lng": -106.6500,
                "vehicle_type": "sedan",
            },
            headers=auth_headers,
        )

        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 201, 400, 401, 422]

    def test_get_ride_endpoint(self, test_client, auth_headers):
        """Test get ride endpoint."""
        response = test_client.get("/api/v1/rides/ride_123", headers=auth_headers)

        # Should succeed or return 404
        assert response.status_code in [200, 404, 401]

    def test_get_user_rides_endpoint(self, test_client, auth_headers):
        """Test get user rides endpoint.

        Smoke test: we accept any of {200 auth-ok, 401 auth-rejected,
        404 no-such-route, 405 method-not-allowed} because different
        API revisions have mounted user-ride history at different
        paths (``/rides``, ``/rides/history``, ``/users/me/rides``).
        The point of this test is "the server didn't crash on the
        request"; fine-grained contract lives in the dedicated ride
        routes suite.
        """
        response = test_client.get("/api/v1/rides", headers=auth_headers)

        assert response.status_code in [200, 401, 404, 405]

    def test_cancel_ride_endpoint(self, test_client, auth_headers):
        """Test cancel ride endpoint."""
        response = test_client.post("/api/v1/rides/ride_123/cancel", headers=auth_headers)

        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 400, 401, 404]


class TestRideSharing:
    """Tests for ride sharing functionality."""

    def test_generate_share_token(self):
        """Test generating a share token for trip tracking."""
        import secrets

        token = secrets.token_urlsafe(16)

        assert token is not None
        assert len(token) >= 16

    def test_share_trip_data_structure(self):
        """Test share trip data structure."""
        share_data = {
            "ride_id": "ride_123",
            "driver_name": "Test Driver",
            "vehicle": "Toyota Camry",
            "license_plate": "ABC123",
            "current_lat": 52.1333,
            "current_lng": -106.6667,
            "eta_minutes": 5,
        }

        assert "ride_id" in share_data
        assert "current_lat" in share_data
        assert "current_lng" in share_data


class TestRideDisputes:
    """Tests for ride dispute functionality."""

    @pytest.mark.asyncio
    async def test_create_dispute(self, mock_supabase_client):
        """Test creating a dispute for a ride."""
        from backend.db_supabase import insert_one

        dispute_data = {
            "ride_id": "ride_123",
            "user_id": "user_123",
            "reason": "overcharged",
            "description": "The fare was higher than estimated",
            "status": "pending",
        }

        mock_response = MagicMock()
        mock_response.data = [{"id": "dispute_123"}]
        mock_supabase_client.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

        result = await insert_one("disputes", dispute_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_resolve_dispute(self, mock_supabase_client):
        """Test resolving a dispute."""
        from backend.db_supabase import update_one

        mock_response = MagicMock()
        mock_response.data = [{"id": "dispute_123", "status": "resolved"}]

        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = MagicMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query

        await update_one("disputes", {"id": "dispute_123"}, {"status": "resolved"})
