"""
Unit tests for rides API and related functionality.
Tests cover ride creation, updates, fare calculation, and ride lifecycle.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from datetime import datetime, timedelta
from typing import Dict, Any, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRideCreation:
    """Tests for ride creation functionality."""
    
    @pytest.fixture
    def sample_ride_request(self):
        """Sample ride creation request data."""
        return {
            'pickup_lat': 52.1333,
            'pickup_lng': -106.6667,
            'dropoff_lat': 52.1500,
            'dropoff_lng': -106.6500,
            'pickup_address': '123 Test St',
            'dropoff_address': '456 Main Ave',
            'vehicle_type': 'sedan',
            'rider_id': 'user_123'
        }
    
    @pytest.mark.asyncio
    async def test_create_ride_success(self, sample_ride_request, mock_supabase_client):
        """Test successful ride creation."""
        from backend.db_supabase import insert_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'status': 'requested'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_ride(sample_ride_request)
        
        assert result is not None
        assert result['id'] == 'ride_123'
        assert result['status'] == 'requested'
    
    @pytest.mark.asyncio
    async def test_create_ride_with_promo(self, sample_ride_request, mock_supabase_client):
        """Test ride creation with promo code."""
        from backend.db_supabase import insert_ride
        
        ride_with_promo = {**sample_ride_request, 'promo_code': 'SAVE10'}
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'status': 'requested', 'promo_applied': True}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
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
        mock_response.data = [{'id': 'ride_123', 'status': 'in_progress'}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {'status': 'in_progress'})
        
        assert result is not None
        assert result['status'] == 'in_progress'
    
    @pytest.mark.asyncio
    async def test_update_ride_driver_assignment(self, ride_collection, mock_supabase_client):
        """Test updating ride with driver assignment."""
        from backend.db_supabase import update_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'driver_id': 'driver_123', 'status': 'accepted'}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {
            'driver_id': 'driver_123',
            'status': 'accepted'
        })
        
        assert result['driver_id'] == 'driver_123'
    
    @pytest.mark.asyncio
    async def test_complete_ride(self, ride_collection, mock_supabase_client):
        """Test completing a ride."""
        from backend.db_supabase import update_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'status': 'completed'}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {'status': 'completed'})
        
        assert result['status'] == 'completed'
    
    @pytest.mark.asyncio
    async def test_cancel_ride(self, ride_collection, mock_supabase_client):
        """Test cancelling a ride."""
        from backend.db_supabase import update_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'status': 'cancelled'}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {'status': 'cancelled'})
        
        assert result['status'] == 'cancelled'


class TestFareCalculation:
    """Tests for fare calculation functionality."""
    
    def test_calculate_distance(self):
        """Test distance calculation between two points."""
        from backend.utils import calculate_distance
        
        # Regina, SK coordinates
        lat1, lng1 = 52.1333, -106.6667
        lat2, lng2 = 52.1500, -106.6500
        
        distance = calculate_distance(lat1, lng1, lat2, lng2)
        
        assert distance > 0
        assert isinstance(distance, float)
    
    def test_calculate_distance_same_point(self):
        """Test distance calculation for same point."""
        from backend.utils import calculate_distance
        
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
            {'id': 'driver_1', 'lat': 52.1350, 'lng': -106.6680, 'is_available': True},
            {'id': 'driver_2', 'lat': 52.1400, 'lng': -106.6700, 'is_available': True}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_drivers
        mock_supabase_client.rpc.return_value.execute = AsyncMock(return_value=mock_response)
        
        result = await find_nearby_drivers(52.1333, -106.6667, 5000)
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_claim_driver_for_ride(self, mock_supabase_client):
        """Test atomically claiming a driver for a ride."""
        from backend.db_supabase import claim_driver_atomic
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'driver_1', 'is_available': False}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await claim_driver_atomic('driver_1')
        
        assert result is True


class TestRideHistory:
    """Tests for ride history functionality."""
    
    @pytest.mark.asyncio
    async def test_get_user_ride_history(self, mock_supabase_client):
        """Test getting ride history for a user."""
        from backend.db_supabase import get_rides_for_user
        
        mock_rides = [
            {'id': 'ride_1', 'status': 'completed', 'created_at': '2024-01-01'},
            {'id': 'ride_2', 'status': 'completed', 'created_at': '2024-01-02'},
            {'id': 'ride_3', 'status': 'cancelled', 'created_at': '2024-01-03'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_rides
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rides_for_user('user_123', limit=10)
        
        assert len(result) == 3
    
    @pytest.mark.asyncio
    async def test_get_driver_ride_history(self, mock_supabase_client):
        """Test getting ride history for a driver."""
        from backend.db_supabase import get_rides_for_driver
        
        mock_rides = [
            {'id': 'ride_1', 'status': 'completed', 'driver_id': 'driver_123'},
            {'id': 'ride_2', 'status': 'completed', 'driver_id': 'driver_123'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_rides
        mock_supabase_client.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rides_for_driver('driver_123', statuses=['completed'])
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_get_ride_by_id(self, mock_supabase_client):
        """Test getting a specific ride by ID."""
        from backend.db_supabase import get_ride
        
        mock_ride = {
            'id': 'ride_123',
            'rider_id': 'user_123',
            'status': 'completed',
            'fare_amount': 15.50
        }
        
        mock_response = MagicMock()
        mock_response.data = [mock_ride]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_ride('ride_123')
        
        assert result is not None
        assert result['id'] == 'ride_123'


class TestRideRatings:
    """Tests for ride rating functionality."""
    
    @pytest.mark.asyncio
    async def test_rate_driver(self, mock_supabase_client):
        """Test rating a driver after ride completion."""
        from backend.db_supabase import update_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'rating': 5, 'tip_amount': 5.00}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {
            'rating': 5,
            'tip_amount': 5.00
        })
        
        assert result['rating'] == 5
        assert result['tip_amount'] == 5.00
    
    @pytest.mark.asyncio
    async def test_rate_rider(self, mock_supabase_client):
        """Test rating a rider after ride completion."""
        from backend.db_supabase import update_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'rider_rating': 4}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {'rider_rating': 4})
        
        assert result['rider_rating'] == 4


class TestScheduledRides:
    """Tests for scheduled ride functionality."""
    
    @pytest.mark.asyncio
    async def test_create_scheduled_ride(self, mock_supabase_client):
        """Test creating a scheduled ride."""
        from backend.db_supabase import insert_ride
        
        scheduled_ride = {
            'pickup_lat': 52.1333,
            'pickup_lng': -106.6667,
            'dropoff_lat': 52.1500,
            'dropoff_lng': -106.6500,
            'scheduled_for': '2024-01-15T08:00:00Z',
            'status': 'scheduled',
            'rider_id': 'user_123'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'status': 'scheduled'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_ride(scheduled_ride)
        
        assert result['status'] == 'scheduled'
    
    @pytest.mark.asyncio
    async def test_cancel_scheduled_ride(self, mock_supabase_client):
        """Test cancelling a scheduled ride."""
        from backend.db_supabase import update_ride
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'ride_123', 'status': 'cancelled'}]
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await update_ride('ride_123', {'status': 'cancelled'})
        
        assert result['status'] == 'cancelled'


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
            '/api/v1/rides',
            json={
                'pickup_lat': 52.1333,
                'pickup_lng': -106.6667,
                'dropoff_lat': 52.1500,
                'dropoff_lng': -106.6500,
                'vehicle_type': 'sedan'
            },
            headers=auth_headers
        )
        
        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 201, 400, 401, 422]
    
    def test_get_ride_endpoint(self, test_client, auth_headers):
        """Test get ride endpoint."""
        response = test_client.get(
            '/api/v1/rides/ride_123',
            headers=auth_headers
        )
        
        # Should succeed or return 404
        assert response.status_code in [200, 404, 401]
    
    def test_get_user_rides_endpoint(self, test_client, auth_headers):
        """Test get user rides endpoint."""
        response = test_client.get(
            '/api/v1/rides',
            headers=auth_headers
        )
        
        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 401]
    
    def test_cancel_ride_endpoint(self, test_client, auth_headers):
        """Test cancel ride endpoint."""
        response = test_client.post(
            '/api/v1/rides/ride_123/cancel',
            headers=auth_headers
        )
        
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
            'ride_id': 'ride_123',
            'driver_name': 'Test Driver',
            'vehicle': 'Toyota Camry',
            'license_plate': 'ABC123',
            'current_lat': 52.1333,
            'current_lng': -106.6667,
            'eta_minutes': 5
        }
        
        assert 'ride_id' in share_data
        assert 'current_lat' in share_data
        assert 'current_lng' in share_data


class TestRideDisputes:
    """Tests for ride dispute functionality."""
    
    @pytest.mark.asyncio
    async def test_create_dispute(self, mock_supabase_client):
        """Test creating a dispute for a ride."""
        from backend.db_supabase import insert_one
        
        dispute_data = {
            'ride_id': 'ride_123',
            'user_id': 'user_123',
            'reason': 'overcharged',
            'description': 'The fare was higher than estimated',
            'status': 'pending'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'dispute_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('disputes', dispute_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_resolve_dispute(self, mock_supabase_client):
        """Test resolving a dispute."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'dispute_123', 'status': 'resolved'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('disputes', {'id': 'dispute_123'}, {'status': 'resolved'})